"""OpenAI-compatible HTTP provider adapter."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from agent_driver.llm.base import HttpClientConfig, ProviderBase, StreamRequest
from agent_driver.llm.contracts import (
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)
from agent_driver.llm.payload_debug import (
    debug_llm_payload_enabled,
    format_payload_debug_line,
)
from agent_driver.llm.provider_capabilities import (
    ProviderCapabilityProfile,
    resolve_openai_compatible_capabilities,
)
from agent_driver.llm.tool_call_parser import extract_text_form_tool_calls
from agent_driver.llm.providers_impl.openai_compatible_normalization import (
    first_choice as _first_choice,
    forced_tool_choice_name as _forced_tool_choice_name,
    map_finish_reason as _map_finish_reason,
    normalize_openai_completion_payload,
    normalize_openai_stream_chunk,
    parse_cost_usd_from_usage as _parse_cost_usd_from_usage,
    planned_tool_call_from_forced_text as _planned_tool_call_from_forced_text,
    planned_tool_calls_from_openai as _planned_tool_calls_from_openai,
    suppress_text_form_tool_calls_when_tools_disabled as _suppress_text_form_tool_calls_when_tools_disabled,
)
from agent_driver.llm.providers_impl.openai_compatible_normalization import (
    estimate_cost_usd as _estimate_cost_usd,
    extract_reasoning_metadata as _extract_reasoning_metadata,
    extract_usage as _extract_usage,
    extract_usage_metadata as _extract_usage_metadata,
    parse_forced_tool_args_fragment as _parse_forced_tool_args_fragment,
    parse_forced_web_search_query_fragment as _parse_forced_web_search_query_fragment,
    parse_json_object_prefix as _parse_json_object_prefix,
)

_LOGGER = logging.getLogger(__name__)


def _log_rejected_request(*, request: LlmRequest, status_code: int, body: str) -> None:
    if not debug_llm_payload_enabled():
        return
    _LOGGER.warning(
        "llm request rejected status=%s payload=%s body=%s",
        status_code,
        format_payload_debug_line(request),
        body[:500],
    )


def _normalize_tool_choice_for_openai(
    value: str | dict[str, Any],
) -> str | dict[str, Any]:
    """Convert the SDK-neutral ``tool_choice`` payload to the OpenAI shape.

    agent-driver lets callers pass a provider-neutral dict form
    ``{"type": "tool", "name": "X"}`` (mirroring the Anthropic shape, which
    is what ``docs/patterns/forcing-tool-calls.md`` documents). OpenAI's
    own API requires a different envelope:

        {"type": "function", "function": {"name": "X"}}

    For OpenAI-compatible relays (OpenAI, OpenRouter, Together, vLLM,
    Groq) we silently translate so callers don't have to learn provider
    quirks. Strings (``"auto"`` / ``"required"`` / ``"none"``) pass
    through unchanged — they are the shared shape across providers.

    Already-OpenAI-shaped dicts (with a top-level ``"function"`` key) are
    returned unchanged so callers who *do* know they're targeting an
    OpenAI backend can pass the native shape without double-wrapping.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return value
    # Already in OpenAI shape — pass through.
    if "function" in value and value.get("type") in (None, "function"):
        return value
    name = value.get("name")
    if value.get("type") == "tool" and isinstance(name, str) and name:
        return {"type": "function", "function": {"name": name}}
    return value


class OpenAICompatibleProvider(ProviderBase):
    """Provider adapter for OpenAI-compatible `/chat/completions` APIs."""

    def __init__(self, *, config: "OpenAICompatibleProvider.Config") -> None:
        super().__init__(
            config=ProviderBase.Config(
                name=config.name,
                kind=LlmProviderKind.OPENAI_COMPATIBLE,
                configured=bool(config.base_url),
                cost_per_1k_tokens=config.cost_per_1k_tokens,
                http_client_config=config.http_client_config,
            )
        )
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key or ""
        self._model = config.model
        self._timeout_s = config.timeout_s
        self._max_tokens_default = config.max_tokens_default
        self._extra_body: dict[str, Any] = dict(config.extra_body or {})
        self._capability_profile = resolve_openai_compatible_capabilities(
            provider_name=config.name,
            base_url=config.base_url,
            model=config.model,
        )
        self.status.metadata["capability_profile"] = (
            self._capability_profile.to_metadata()
        )

    @dataclass(slots=True)
    class Config:
        """OpenAI-compatible provider connection and model settings."""

        name: str
        base_url: str
        api_key: str | None
        model: str
        timeout_s: float = 30.0
        max_tokens_default: int | None = 4096
        cost_per_1k_tokens: float = 0.0
        http_client_config: HttpClientConfig | None = None
        # Vendor-specific extra fields merged into every chat/completions
        # request body (e.g. vLLM ``chat_template_kwargs`` for Qwen3
        # ``enable_thinking``, OpenRouter ``provider`` routing hints,
        # Anthropic ``system`` overrides). Shallow-merged into the
        # payload after the standard fields are built, so vendor keys
        # take precedence on collision — keep this dict to vendor-only
        # keys to avoid clobbering ``messages`` / ``model`` / ``stream``.
        extra_body: dict[str, Any] | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @property
    def capability_profile(self) -> ProviderCapabilityProfile:
        """Best-effort provider/model capability profile."""
        return self._capability_profile

    def _with_capability_metadata(self, response: LlmResponse) -> LlmResponse:
        metadata = {
            **response.metadata,
            "provider_profile": self._capability_profile.to_metadata(),
        }
        return response.model_copy(update={"metadata": metadata})

    def _event_with_capability_metadata(self, event: LlmStreamEvent) -> LlmStreamEvent:
        metadata = {
            **event.metadata,
            "provider_profile": self._capability_profile.to_metadata(),
        }
        return event.model_copy(update={"metadata": metadata})

    def _payload(self, request: LlmRequest, *, stream: bool) -> dict[str, Any]:
        from agent_driver.llm.tool_result_unpacker import (
            build_openai_tool_content_list,
        )

        messages_payload: list[dict[str, Any]] = []
        for message in request.messages:
            # Phase 13 H29.2 — when a tool-role message carries binary
            # attachments (e.g. screenshot images planted in metadata by
            # ``tool_stage``), emit the OpenAI ``content`` list shape
            # with text + image_url blocks instead of the flat string.
            attachments = message.metadata.get("attachments")
            content_blocks: list[dict[str, Any]] | None = None
            if (
                message.role.value == "tool"
                and isinstance(attachments, list)
                and attachments
            ):
                content_blocks = build_openai_tool_content_list(
                    message.content, attachments
                )
            row: dict[str, Any] = {
                "role": message.role.value,
                "content": (
                    content_blocks if content_blocks is not None else message.content
                ),
            }
            if message.name:
                row["name"] = message.name
            if message.tool_call_id:
                row["tool_call_id"] = message.tool_call_id
            tool_calls = message.metadata.get("tool_calls")
            if (
                message.role.value == "assistant"
                and isinstance(tool_calls, list)
                and tool_calls
            ):
                row["tool_calls"] = tool_calls
            reasoning_details = message.metadata.get("reasoning_details")
            if (
                message.role.value == "assistant"
                and isinstance(reasoning_details, list)
                and reasoning_details
            ):
                row["reasoning_details"] = reasoning_details
            reasoning = message.metadata.get("reasoning")
            if (
                message.role.value == "assistant"
                and isinstance(reasoning, str)
                and reasoning
            ):
                row["reasoning"] = reasoning
            messages_payload.append(row)
        payload = {
            "model": request.model or self._model,
            "messages": messages_payload,
            "stream": stream,
        }
        max_tokens = (
            request.max_tokens
            if request.max_tokens is not None
            else self._max_tokens_default
        )
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = _normalize_tool_choice_for_openai(
                request.tool_choice if request.tool_choice is not None else "auto"
            )
            # Phase 13 H29 — emit ``parallel_tool_calls`` only when the
            # caller explicitly set it. None means "use provider default"
            # (most backends are True), so omitting the key avoids
            # accidental opt-out on backends that default differently.
            if request.parallel_tool_calls is not None:
                payload["parallel_tool_calls"] = request.parallel_tool_calls
        elif request.tool_choice is not None:
            payload["tool_choice"] = _normalize_tool_choice_for_openai(
                request.tool_choice
            )
        # Phase 13 H26 — structured output enforcement at the provider
        # layer. Pass through the native OpenAI ``response_format`` shape
        # when the caller set it; omit entirely when None so we don't
        # accidentally activate enforcement on backends that interpret the
        # presence of the key (even with permissive values) differently.
        # Vendor-specific re-routing (e.g. vLLM ``guided_json``) is the
        # responsibility of ``extra_body`` below.
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        # Vendor-specific extras (e.g. vLLM ``chat_template_kwargs``,
        # OpenRouter ``provider`` hints) — merged last so they win on
        # collision with the standard openai-compat keys.
        if self._extra_body:
            for key, value in self._extra_body.items():
                payload[key] = value
        request_extra_body = request.metadata.get("provider_extra_body")
        if isinstance(request_extra_body, dict):
            for key, value in request_extra_body.items():
                payload[key] = value
        return payload

    async def healthcheck(self) -> ProviderStatus:
        """Probe provider endpoint availability."""
        started = time.monotonic()
        url = f"{self._base_url}/models"
        try:
            async with self.build_async_client(timeout_s=self._timeout_s) as client:
                response = await client.get(url, headers=self._headers())
            elapsed_ms = (time.monotonic() - started) * 1000
            self.status.latency_ms = elapsed_ms
            self.status.avg_latency_ms = elapsed_ms
            self.status.healthy = response.status_code in {200, 400, 401, 403}
        except (httpx.HTTPError, OSError):
            self.status.healthy = False
        return self.status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Execute non-streaming completion call."""
        url = f"{self._base_url}/chat/completions"

        async def _op() -> LlmResponse:
            async with self.build_async_client(timeout_s=self._timeout_s) as client:
                response = await client.post(
                    url,
                    headers=self._headers(),
                    json=self._payload(request, stream=False),
                )
            if response.status_code >= 400:
                _log_rejected_request(
                    request=request,
                    status_code=response.status_code,
                    body=response.text,
                )
            response.raise_for_status()
            return self._with_capability_metadata(
                normalize_openai_completion_payload(
                    response.json(),
                    provider_name=self.name,
                    fallback_model=str(request.model or self._model),
                    cost_per_1k_tokens=float(self.status.cost_per_1k_tokens or 0.0),
                )
            )

        return await self.execute_with_telemetry(
            _op, handled_exceptions=(httpx.HTTPError, ValueError)
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Execute streaming completion call and normalize deltas."""
        url = f"{self._base_url}/chat/completions"
        handled_errors = (httpx.HTTPError, ValueError)
        stream_request = StreamRequest(
            timeout_s=self._timeout_s,
            method="POST",
            url=url,
            headers=self._headers(),
            json=self._payload(request, stream=True),
            handled_exceptions=handled_errors,
        )
        async with self.stream_client_with_telemetry(stream_request) as lines:
            pending_tool_calls: dict[int, dict[str, Any]] = {}
            text_chunks: list[str] = []
            forced_tool_name = _forced_tool_choice_name(request.tool_choice)
            async for line in lines:
                if not line or not line.startswith("data: "):
                    continue
                raw = line[len("data: ") :]
                if raw.strip() == "[DONE]":
                    break
                payload = httpx.Response(200, text=raw).json()
                choice = _first_choice(payload)
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        text_chunks.append(content)
                    for entry in delta.get("tool_calls", []) or []:
                        if not isinstance(entry, dict):
                            continue
                        try:
                            index = int(entry.get("index", 0) or 0)
                        except (TypeError, ValueError):
                            index = 0
                        function = entry.get("function")
                        state = pending_tool_calls.setdefault(
                            index,
                            {
                                "id": entry.get("id"),
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if isinstance(entry.get("id"), str) and entry.get("id"):
                            state["id"] = entry["id"]
                        if isinstance(function, dict):
                            state_fn = state.setdefault(
                                "function", {"name": "", "arguments": ""}
                            )
                            if isinstance(function.get("name"), str):
                                state_fn["name"] = (
                                    f"{state_fn.get('name', '')}{function['name']}"
                                )
                            if isinstance(function.get("arguments"), str):
                                state_fn["arguments"] = (
                                    f"{state_fn.get('arguments', '')}{function['arguments']}"
                                )
                event = normalize_openai_stream_chunk(
                    payload,
                    provider_name=self.name,
                    fallback_model=str(request.model or self._model),
                    cost_per_1k_tokens=float(self.status.cost_per_1k_tokens or 0.0),
                )
                yield self._event_with_capability_metadata(
                    _suppress_text_form_tool_calls_when_tools_disabled(
                        event,
                        tool_choice=request.tool_choice,
                    )
                )
            if pending_tool_calls:
                flattened = [
                    pending_tool_calls[idx] for idx in sorted(pending_tool_calls)
                ]
                planned_tool_calls, parse_errors = _planned_tool_calls_from_openai(
                    flattened
                )
                if planned_tool_calls or parse_errors:
                    metadata: dict[str, Any] = {}
                    if planned_tool_calls:
                        metadata["planned_tool_calls"] = planned_tool_calls
                    if parse_errors:
                        metadata["tool_call_parse_errors"] = parse_errors
                    yield self._event_with_capability_metadata(
                        LlmStreamEvent(event="tool_calls", metadata=metadata)
                    )
            elif text_chunks and request.tool_choice != "none":
                text = "".join(text_chunks)
                text_planned, text_errors = extract_text_form_tool_calls(text)
                if not text_planned:
                    text_planned = _planned_tool_call_from_forced_text(
                        tool_name=forced_tool_name,
                        text=text,
                    )
                if text_planned or text_errors:
                    metadata = {"text_form_tool_calls_parsed": True}
                    if text_planned:
                        metadata["planned_tool_calls"] = text_planned
                    if text_errors:
                        metadata["tool_call_parse_errors"] = text_errors
                    yield self._event_with_capability_metadata(
                        LlmStreamEvent(event="tool_calls", metadata=metadata)
                    )


__all__ = [
    "OpenAICompatibleProvider",
    "_estimate_cost_usd",
    "_extract_reasoning_metadata",
    "_extract_usage",
    "_extract_usage_metadata",
    "_first_choice",
    "_forced_tool_choice_name",
    "_map_finish_reason",
    "normalize_openai_completion_payload",
    "normalize_openai_stream_chunk",
    "_parse_cost_usd_from_usage",
    "_parse_forced_tool_args_fragment",
    "_parse_forced_web_search_query_fragment",
    "_parse_json_object_prefix",
    "_planned_tool_call_from_forced_text",
    "_planned_tool_calls_from_openai",
    "_suppress_text_form_tool_calls_when_tools_disabled",
]
