"""OpenAI-compatible HTTP provider adapter."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.base import ProviderBase
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)


def _map_finish_reason(reason: str | None) -> LlmFinishReason:
    if reason == "stop":
        return LlmFinishReason.STOP
    if reason in {"length", "max_tokens"}:
        return LlmFinishReason.LENGTH
    if reason in {"tool_calls", "function_call"}:
        return LlmFinishReason.TOOL_CALLS
    if reason == "error":
        return LlmFinishReason.ERROR
    return LlmFinishReason.UNKNOWN


def _extract_usage(
    payload: dict[str, Any], *, provider: str, model: str
) -> UsageSummary:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(
        usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
    )
    return UsageSummary(
        input_tokens=max(0, prompt_tokens),
        output_tokens=max(0, completion_tokens),
        total_tokens=max(0, total_tokens),
        model_provider=provider,
        model_name=model,
    )


def normalize_openai_completion_payload(
    payload: dict[str, Any], *, provider_name: str, fallback_model: str
) -> LlmResponse:
    """Normalize OpenAI-compatible completion payload to provider-neutral response."""
    choice = payload.get("choices", [{}])[0]
    message_payload = choice.get("message", {}) if isinstance(choice, dict) else {}
    text = str(message_payload.get("content", "") or "")
    model_name = str(payload.get("model") or fallback_model)
    usage = _extract_usage(payload, provider=provider_name, model=model_name)
    return LlmResponse(
        message=ChatMessage(role="assistant", content=text),
        finish_reason=_map_finish_reason(choice.get("finish_reason")),
        usage=usage,
        provider=provider_name,
        model=model_name,
        raw_response=payload if isinstance(payload, dict) else {},
    )


def normalize_openai_stream_chunk(
    payload: dict[str, Any], *, provider_name: str, fallback_model: str
) -> LlmStreamEvent:
    """Normalize one OpenAI-compatible stream chunk."""
    choice = payload.get("choices", [{}])[0]
    delta = choice.get("delta", {}) if isinstance(choice, dict) else {}
    text = str(delta.get("content", "") or "")
    finish_reason = _map_finish_reason(choice.get("finish_reason"))
    usage = (
        _extract_usage(payload, provider=provider_name, model=fallback_model)
        if isinstance(payload, dict) and payload.get("usage")
        else None
    )
    return LlmStreamEvent(
        event="delta",
        delta_text=text,
        finish_reason=(
            finish_reason if finish_reason != LlmFinishReason.UNKNOWN else None
        ),
        usage=usage,
    )


class OpenAICompatibleProvider(ProviderBase):
    """Provider adapter for OpenAI-compatible `/chat/completions` APIs."""

    def __init__(self, *, config: "OpenAICompatibleProvider.Config") -> None:
        super().__init__(
            name=config.name,
            kind=LlmProviderKind.OPENAI_COMPATIBLE,
            configured=bool(config.base_url),
            cost_per_1k_tokens=config.cost_per_1k_tokens,
        )
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key or ""
        self._model = config.model
        self._timeout_s = config.timeout_s

    @dataclass(slots=True)
    class Config:
        """OpenAI-compatible provider connection and model settings."""

        name: str
        base_url: str
        api_key: str | None
        model: str
        timeout_s: float = 30.0
        cost_per_1k_tokens: float = 0.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(self, request: LlmRequest, *, stream: bool) -> dict[str, Any]:
        return {
            "model": request.model or self._model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }

    async def healthcheck(self) -> ProviderStatus:
        """Probe provider endpoint availability."""
        started = time.monotonic()
        url = f"{self._base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(url, headers=self._headers())
            elapsed_ms = (time.monotonic() - started) * 1000
            self.status.latency_ms = elapsed_ms
            self.status.avg_latency_ms = elapsed_ms
            self.status.healthy = response.status_code in {200, 400, 401, 403}
        except httpx.HTTPError:
            self.status.healthy = False
        return self.status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Execute non-streaming completion call."""
        url = f"{self._base_url}/chat/completions"

        async def _op() -> LlmResponse:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    url,
                    headers=self._headers(),
                    json=self._payload(request, stream=False),
                )
            response.raise_for_status()
            return normalize_openai_completion_payload(
                response.json(),
                provider_name=self.name,
                fallback_model=str(request.model or self._model),
            )

        return await self.execute_with_telemetry(
            _op, handled_exceptions=(httpx.HTTPError, ValueError)
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Execute streaming completion call and normalize deltas."""
        url = f"{self._base_url}/chat/completions"

        async def _collect() -> list[LlmStreamEvent]:
            chunks: list[LlmStreamEvent] = []
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._headers(),
                    json=self._payload(request, stream=True),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        raw = line[len("data: ") :]
                        if raw.strip() == "[DONE]":
                            break
                        payload = httpx.Response(200, text=raw).json()
                        chunks.append(
                            normalize_openai_stream_chunk(
                                payload,
                                provider_name=self.name,
                                fallback_model=str(request.model or self._model),
                            )
                        )
            return chunks

        events = await self.execute_with_telemetry(
            _collect, handled_exceptions=(httpx.HTTPError, ValueError)
        )
        for event in events:
            yield event
