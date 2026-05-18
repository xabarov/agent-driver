"""Ollama provider adapter."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.base import ProviderBase, StreamRequest
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)


def _ollama_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [
        {"role": message.role.value, "content": message.content} for message in messages
    ]


def normalize_ollama_completion_payload(
    payload: dict[str, Any], *, provider_name: str, fallback_model: str
) -> LlmResponse:
    """Normalize non-stream Ollama payload to provider-neutral response."""
    text = str((payload.get("message") or {}).get("content") or "")
    model_name = str(payload.get("model") or fallback_model)
    usage = UsageSummary(
        input_tokens=int(payload.get("prompt_eval_count", 0) or 0),
        output_tokens=int(payload.get("eval_count", 0) or 0),
        total_tokens=int(payload.get("prompt_eval_count", 0) or 0)
        + int(payload.get("eval_count", 0) or 0),
        model_provider=provider_name,
        model_name=model_name,
    )
    return LlmResponse(
        message=ChatMessage(role="assistant", content=text),
        finish_reason=LlmFinishReason.STOP,
        usage=usage,
        provider=provider_name,
        model=model_name,
        raw_response=payload if isinstance(payload, dict) else {},
    )


def normalize_ollama_stream_chunk(
    chunk: dict[str, Any], *, provider_name: str, fallback_model: str
) -> LlmStreamEvent:
    """Normalize one stream chunk returned by Ollama."""
    delta = str((chunk.get("message") or {}).get("content") or "")
    done = bool(chunk.get("done"))
    usage = None
    finish_reason = None
    if done:
        finish_reason = LlmFinishReason.STOP
        usage = UsageSummary(
            input_tokens=int(chunk.get("prompt_eval_count", 0) or 0),
            output_tokens=int(chunk.get("eval_count", 0) or 0),
            total_tokens=int(chunk.get("prompt_eval_count", 0) or 0)
            + int(chunk.get("eval_count", 0) or 0),
            model_provider=provider_name,
            model_name=fallback_model,
        )
    return LlmStreamEvent(
        event="delta" if not done else "done",
        delta_text=delta,
        finish_reason=finish_reason,
        usage=usage,
    )


class OllamaProvider(ProviderBase):
    """Provider adapter for Ollama `/api/chat` endpoint."""

    def __init__(
        self,
        *,
        name: str = "ollama",
        base_url: str = "http://localhost:11434",
        model: str = "llama3:8b",
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__(
            name=name,
            kind=LlmProviderKind.OLLAMA,
            configured=bool(base_url),
            cost_per_1k_tokens=0.0,
        )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s

    async def healthcheck(self) -> ProviderStatus:
        """Probe Ollama tags endpoint."""
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(f"{self._base_url}/api/tags")
            elapsed_ms = (time.monotonic() - started) * 1000
            self.status.latency_ms = elapsed_ms
            self.status.avg_latency_ms = elapsed_ms
            self.status.healthy = response.status_code == 200
        except httpx.HTTPError:
            self.status.healthy = False
        return self.status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Execute non-streaming Ollama chat request."""
        payload = {
            "model": request.model or self._model,
            "messages": _ollama_messages(request.messages),
            "stream": False,
            "options": {
                "num_predict": request.max_tokens,
                "temperature": request.temperature,
            },
        }

        async def _op() -> LlmResponse:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
            return normalize_ollama_completion_payload(
                response.json(),
                provider_name=self.name,
                fallback_model=str(request.model or self._model),
            )

        handled_errors = (httpx.HTTPError, ValueError)
        completion = await self.execute_with_telemetry(
            _op, handled_exceptions=handled_errors
        )
        return completion

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Execute streaming Ollama chat request."""
        payload = {
            "model": request.model or self._model,
            "messages": _ollama_messages(request.messages),
            "stream": True,
            "options": {
                "num_predict": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        handled_errors = (httpx.HTTPError, ValueError)
        stream_request = StreamRequest(
            timeout_s=self._timeout_s,
            method="POST",
            url=f"{self._base_url}/api/chat",
            json=payload,
            handled_exceptions=handled_errors,
        )
        async with self.stream_client_with_telemetry(stream_request) as lines:
            async for line in lines:
                if not line:
                    continue
                chunk = httpx.Response(200, text=line).json()
                event = normalize_ollama_stream_chunk(
                    chunk,
                    provider_name=self.name,
                    fallback_model=str(request.model or self._model),
                )
                yield event
                if event.event == "done":
                    break
