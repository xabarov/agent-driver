"""OpenAI-compatible HTTP provider adapter."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from agent_driver.llm.base import (
    HttpClientConfig,
    ProviderBase,
    StreamRequest,
    provider_request_id,
)
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
from agent_driver.llm.providers_impl.openai_compatible.payload import (
    build_openai_completion_payload,
    normalize_tool_choice_for_openai as _normalize_tool_choice_for_openai,
)
from agent_driver.llm.provider_capabilities import (
    ProviderCapabilityProfile,
    resolve_openai_compatible_capabilities,
)
from agent_driver.llm.tool_call_parser import (
    extract_text_form_tool_call_details,
    strip_text_form_tool_call_ranges,
)
from agent_driver.llm.providers_impl.openai_compatible.normalization import (
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
from agent_driver.llm.providers_impl.openai_compatible.normalization import (
    estimate_cost_usd as _estimate_cost_usd,
    extract_reasoning_metadata as _extract_reasoning_metadata,
    extract_usage as _extract_usage,
    extract_usage_metadata as _extract_usage_metadata,
    parse_forced_tool_args_fragment as _parse_forced_tool_args_fragment,
    parse_forced_web_search_query_fragment as _parse_forced_web_search_query_fragment,
    parse_json_object_prefix as _parse_json_object_prefix,
)

_LOGGER = logging.getLogger(__name__)
_TEXT_FORM_STREAM_HOLDBACK_CHARS = 64
_TEXT_FORM_OPENER_PATTERNS = (
    re.compile(r"<\s*tool_call\b", re.IGNORECASE),
    re.compile(r"tool_call\s*:", re.IGNORECASE),
    re.compile(r"<\|\s*python_tag\|>", re.IGNORECASE),
    re.compile(r"<\|?\s*tool_call|<\s*tool_call\s*\|", re.IGNORECASE),
    re.compile(r"<\s*[｜|]+\s*DSML\b", re.IGNORECASE),
)
_TEXT_FORM_OPENER_ALIASES = (
    "<tool_call",
    "< tool_call",
    "<|tool_call",
    "<|python_tag|>",
    "<｜dsml",
    "<|dsml",
    "tool_call:",
)


def _log_rejected_request(*, request: LlmRequest, status_code: int, body: str) -> None:
    if not debug_llm_payload_enabled():
        return
    _LOGGER.warning(
        "llm request rejected status=%s payload=%s body=%s",
        status_code,
        format_payload_debug_line(request),
        body[:500],
    )


def _first_text_form_opener_start(text: str) -> int | None:
    starts = [
        m.start()
        for pattern in _TEXT_FORM_OPENER_PATTERNS
        if (m := pattern.search(text))
    ]
    if not starts:
        return None
    return min(starts)


def _split_stream_visible_text(buffer: str) -> tuple[str, str]:
    """Return (safe_to_emit_now, pending_buffer) for text-form tool-call holdback."""
    if not buffer:
        return "", ""
    opener_start = _first_text_form_opener_start(buffer)
    if opener_start is not None:
        return buffer[:opener_start], buffer[opener_start:]
    partial_start = _partial_text_form_opener_tail_start(buffer)
    if partial_start is not None:
        return buffer[:partial_start], buffer[partial_start:]
    return buffer, ""


def _partial_text_form_opener_tail_start(buffer: str) -> int | None:
    tail_start = max(0, len(buffer) - _TEXT_FORM_STREAM_HOLDBACK_CHARS)
    lower = buffer.lower()
    for index in range(tail_start, len(buffer)):
        fragment = lower[index:]
        if any(alias.startswith(fragment) for alias in _TEXT_FORM_OPENER_ALIASES):
            return index
    if re.search(r"<\s*$", buffer[tail_start:]):
        return buffer.rfind("<")
    return None


def _flush_stream_visible_text(buffer: str) -> tuple[str, dict[str, Any]]:
    """Strip recognized text-form tool-call ranges from held stream text."""
    if not buffer:
        return "", {}
    details = extract_text_form_tool_call_details(buffer)
    metadata: dict[str, Any] = {}
    if details.tool_calls or details.parse_errors:
        metadata["text_form_tool_calls_parsed"] = True
    if details.ranges:
        metadata["text_form_tool_call_ranges"] = details.ranges
        return strip_text_form_tool_call_ranges(buffer, details.ranges), metadata
    return buffer, metadata


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
        return build_openai_completion_payload(
            request,
            model=self._model,
            max_tokens_default=self._max_tokens_default,
            extra_body=self._extra_body,
            stream=stream,
        )

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
            llm_response = normalize_openai_completion_payload(
                response.json(),
                provider_name=self.name,
                fallback_model=str(request.model or self._model),
                cost_per_1k_tokens=float(self.status.cost_per_1k_tokens or 0.0),
            )
            request_id = provider_request_id(response.headers)
            if request_id:
                llm_response = llm_response.model_copy(
                    update={
                        "metadata": {
                            **llm_response.metadata,
                            "provider_request_id": request_id,
                        }
                    }
                )
            return self._with_capability_metadata(llm_response)

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
            held_text = ""
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
                if event.delta_text:
                    original_delta_text = event.delta_text
                    held_text += event.delta_text
                    visible_text, held_text = _split_stream_visible_text(held_text)
                    update: dict[str, Any] = {"delta_text": visible_text}
                    if (
                        not visible_text
                        and held_text
                        and _first_text_form_opener_start(held_text) is not None
                    ):
                        update["metadata"] = {
                            **event.metadata,
                            "text_form_tool_call_holdback": True,
                        }
                    elif visible_text != original_delta_text and held_text:
                        update["metadata"] = {
                            **event.metadata,
                            "text_form_tool_call_holdback": True,
                        }
                    event = event.model_copy(update=update)
                yield self._event_with_capability_metadata(
                    _suppress_text_form_tool_calls_when_tools_disabled(
                        event,
                        tool_choice=request.tool_choice,
                    )
                )
            if held_text:
                visible_text, visible_metadata = _flush_stream_visible_text(held_text)
                if request.tool_choice == "none" and visible_metadata:
                    visible_metadata["text_form_tool_calls_suppressed"] = True
                    visible_metadata.pop("planned_tool_calls", None)
                if visible_text or visible_metadata:
                    yield self._event_with_capability_metadata(
                        LlmStreamEvent(
                            event="delta",
                            delta_text=visible_text,
                            metadata=visible_metadata,
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
                details = extract_text_form_tool_call_details(text)
                text_planned, text_errors = details.tool_calls, details.parse_errors
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
                    if details.ranges:
                        metadata["text_form_tool_call_ranges"] = details.ranges
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
    "_normalize_tool_choice_for_openai",
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
