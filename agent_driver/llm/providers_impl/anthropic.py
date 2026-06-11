"""Anthropic-native provider adapter.

Implements the Messages API (https://api.anthropic.com/v1/messages) directly
rather than going through OpenAI-compatible shims. Key differences from
OpenAI:

- Auth header is ``x-api-key`` (not ``Authorization: Bearer``).
- ``anthropic-version: 2023-06-01`` header is required.
- ``system`` is a top-level request field, NOT a message in ``messages``.
- ``messages`` must alternate user/assistant; consecutive same-role messages
  are merged into a single content array.
- Response ``content`` is an array of typed blocks (``text``, ``tool_use``);
  this provider extracts text content. Tool-use blocks land in
  ``raw_response`` for downstream consumers.
- Stream events are typed SSE: ``message_start``, ``content_block_start``,
  ``content_block_delta`` (per-token text), ``content_block_stop``,
  ``message_delta`` (carries ``stop_reason``), ``message_stop``.
- Stop reasons: ``end_turn`` / ``max_tokens`` / ``tool_use`` /
  ``stop_sequence`` — mapped to ``LlmFinishReason``.

Streaming + tool use are supported. Tool-use stream events emit a single
``LlmStreamEvent`` per tool_use block with the block ID + name in
``metadata`` so callers can correlate input deltas (``input_json_delta``).
This is the minimum surface needed to register the provider and use it
for chat; richer tool-use streaming (e.g. partial JSON args) is a
follow-up slice.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.base import (
    HttpClientConfig,
    ProviderBase,
    StreamRequest,
    provider_request_id,
)
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


def _map_stop_reason(reason: str | None) -> LlmFinishReason:
    """Map an Anthropic stop reason to the provider-neutral finish enum."""
    if reason in (None, "", "end_turn", "stop_sequence"):
        return LlmFinishReason.STOP if reason else LlmFinishReason.UNKNOWN
    if reason == "max_tokens":
        return LlmFinishReason.LENGTH
    if reason == "tool_use":
        return LlmFinishReason.TOOL_CALLS
    return LlmFinishReason.UNKNOWN


def _normalize_messages(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    """Split ``ChatMessage`` list into (system_prompt, anthropic_messages).

    Anthropic requires:
      - ``system`` is a top-level string (or array of content blocks, but we
        flatten to text for simplicity).
      - ``messages`` must alternate user/assistant; consecutive same-role
        messages are merged into a single message with concatenated content.
      - The first message must be ``user``. We don't enforce that here —
        callers responsible for assembling valid conversations; provider
        passes through and lets Anthropic's API surface validation errors.
    """
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.role.value if hasattr(message.role, "value") else str(message.role)
        if role == "system":
            if message.content:
                system_parts.append(str(message.content))
            continue
        if role not in ("user", "assistant", "tool"):
            # Unknown role — treat as user to keep API call valid.
            role = "user"
        # Tool role messages → user role with tool_result content block.
        if role == "tool":
            tool_use_id = ""
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id:
                tool_use_id = str(tool_call_id)
            block: dict[str, Any] = {
                "type": "tool_result",
                "content": str(message.content or ""),
            }
            if tool_use_id:
                block["tool_use_id"] = tool_use_id
            converted.append({"role": "user", "content": [block]})
            continue
        # Merge consecutive same-role messages (Anthropic requires alternation).
        if converted and converted[-1]["role"] == role:
            prev_content = converted[-1]["content"]
            if isinstance(prev_content, str):
                prev_content = [{"type": "text", "text": prev_content}]
            prev_content.append({"type": "text", "text": str(message.content or "")})
            converted[-1]["content"] = prev_content
            continue
        converted.append({"role": role, "content": str(message.content or "")})
    return "\n".join(system_parts), converted


def _mark_last_message_for_cache(messages: list[dict[str, Any]]) -> None:
    """Attach an ephemeral cache breakpoint to the final message in place.

    Anthropic caches the request prefix up to and including the outermost
    ``cache_control`` marker. Marking the last message means the entire
    conversation sent this turn becomes the cached prefix the *next* turn reads
    back (the next turn only appends), so a growing multi-turn history is billed
    at cache-read rates instead of re-sending the whole transcript each turn.
    Content is normalized to a block array so the marker rides the last block.
    """
    if not messages:
        return
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
        last["content"] = content
    if isinstance(content, list) and content:
        # Shallow-copy the final block so we never mutate a caller-shared dict.
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


def _extract_text_from_content(content: Any) -> str:
    """Pull text from an Anthropic response ``content`` array."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _usage_from_payload(
    payload: dict[str, Any], *, provider_name: str, model_name: str
) -> UsageSummary:
    raw_usage = payload.get("usage") or {}
    input_tokens = int(raw_usage.get("input_tokens") or 0)
    output_tokens = int(raw_usage.get("output_tokens") or 0)
    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        model_provider=provider_name,
        model_name=model_name,
    )


def normalize_anthropic_completion_payload(
    payload: dict[str, Any], *, provider_name: str, fallback_model: str
) -> LlmResponse:
    """Build an LlmResponse from an Anthropic /v1/messages response."""
    model_name = str(payload.get("model") or fallback_model)
    text = _extract_text_from_content(payload.get("content"))
    finish = _map_stop_reason(payload.get("stop_reason"))
    usage = _usage_from_payload(payload, provider_name=provider_name, model_name=model_name)
    return LlmResponse(
        message=ChatMessage(role="assistant", content=text),
        finish_reason=finish,
        usage=usage,
        provider=provider_name,
        model=model_name,
        raw_response=payload if isinstance(payload, dict) else {},
        metadata={"provider_usage_raw": payload} if payload else {},
    )


def _parse_sse_event_line(buffer: list[str]) -> tuple[str, dict[str, Any]] | None:
    """Pop a complete SSE event from a list of accumulated lines.

    Returns ``(event_type, data_json_dict)`` or None when no complete event
    is present yet. Anthropic SSE shape:

        event: message_start
        data: {"type": "message_start", ...}

        event: content_block_delta
        data: {"type": "content_block_delta", "delta": {"text": "..."}}

    Both ``event:`` and ``data:`` lines are required for the events we care
    about; ``: ping`` and similar keep-alives are ignored.
    """
    event_name = ""
    data_payload = ""
    for line in buffer:
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_payload = line[len("data:") :].strip()
    if not event_name or not data_payload:
        return None
    try:
        data_obj = json.loads(data_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data_obj, dict):
        return None
    return event_name, data_obj


def _stream_event_from_anthropic(
    event_name: str,
    data: dict[str, Any],
    *,
    provider_name: str,
    model_name: str,
) -> LlmStreamEvent | None:
    """Convert one Anthropic SSE event into an LlmStreamEvent.

    Returns None when the event should be silently dropped (e.g. message_start
    has no text content; content_block_start for tool_use is recorded only
    via metadata on the first delta).
    """
    if event_name == "content_block_delta":
        delta = data.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            return LlmStreamEvent(
                event="delta",
                delta_text=str(delta.get("text") or ""),
            )
        if delta_type == "input_json_delta":
            # Partial tool-use args — surface as metadata only for now.
            return LlmStreamEvent(
                event="delta",
                metadata={"tool_input_json_partial": str(delta.get("partial_json") or "")},
            )
        return None
    if event_name == "message_delta":
        delta = data.get("delta") or {}
        stop_reason = delta.get("stop_reason")
        usage_raw = data.get("usage") or {}
        usage = None
        if usage_raw:
            output_tokens = int(usage_raw.get("output_tokens") or 0)
            usage = UsageSummary(
                input_tokens=0,  # cumulative input is on message_start
                output_tokens=output_tokens,
                total_tokens=output_tokens,
                model_provider=provider_name,
                model_name=model_name,
            )
        return LlmStreamEvent(
            event="done" if stop_reason else "delta",
            finish_reason=_map_stop_reason(stop_reason) if stop_reason else None,
            usage=usage,
        )
    if event_name == "message_stop":
        return LlmStreamEvent(event="done")
    # message_start, content_block_start/stop, ping — not surfaced.
    return None


class AnthropicProvider(ProviderBase):
    """Provider adapter for the Anthropic Messages API."""

    @dataclass(slots=True)
    class Config:
        """Anthropic provider connection and model settings."""

        name: str = "anthropic"
        base_url: str = _DEFAULT_BASE_URL
        api_key: str = ""
        model: str = "claude-3-5-haiku-latest"
        anthropic_version: str = _DEFAULT_ANTHROPIC_VERSION
        max_tokens_default: int = 4096
        timeout_s: float = 60.0
        cost_per_1k_tokens: float = 0.0
        extra_headers: dict[str, str] = field(default_factory=dict)
        http_client_config: HttpClientConfig | None = None

    def __init__(self, *, config: "AnthropicProvider.Config" | None = None) -> None:
        cfg = config or AnthropicProvider.Config()
        super().__init__(
            config=ProviderBase.Config(
                name=cfg.name,
                kind=LlmProviderKind.ANTHROPIC,
                configured=bool(cfg.api_key),
                cost_per_1k_tokens=cfg.cost_per_1k_tokens,
                http_client_config=cfg.http_client_config,
            )
        )
        self._base_url = cfg.base_url.rstrip("/")
        self._api_key = cfg.api_key
        self._model = cfg.model
        self._anthropic_version = cfg.anthropic_version
        self._max_tokens_default = cfg.max_tokens_default
        self._timeout_s = cfg.timeout_s
        self._extra_headers = dict(cfg.extra_headers)

    def _headers(self, *, stream: bool) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "anthropic-version": self._anthropic_version,
            "x-api-key": self._api_key,
        }
        if stream:
            headers["accept"] = "text/event-stream"
        headers.update(self._extra_headers)
        return headers

    def _request_payload(self, request: LlmRequest, *, stream: bool) -> dict[str, Any]:
        system_text, messages = _normalize_messages(request.messages)
        if request.enable_prompt_cache:
            # Third cache breakpoint (after tools + system): the conversation.
            # Together they tier the prefix static-tools → system → transcript,
            # so each layer that is unchanged next turn reads from cache.
            _mark_last_message_for_cache(messages)
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": messages,
            "max_tokens": request.max_tokens or self._max_tokens_default,
            "stream": stream,
        }
        if system_text:
            # Phase 13 H24 — when prompt caching is opted in, emit ``system``
            # as a content-block array with cache_control: ephemeral. The
            # marker tells Anthropic to cache everything up to and including
            # the system prompt for ~5 minutes (ephemeral TTL), so subsequent
            # requests with the same prefix get ``cache_read_input_tokens``
            # instead of full input-rate billing. The string form (without
            # cache_control) stays default for callers that haven't opted in.
            if request.enable_prompt_cache:
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                payload["system"] = system_text
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            tools_list = list(request.tools)
            # Phase 13 H24 — Anthropic's cache marker on the LAST tool
            # extends the cached prefix to include the entire tools catalog.
            # We attach to the final tool only (markers on earlier tools
            # would be redundant — caching is always cumulative up to the
            # outermost marker). Tool dicts are shallow-copied so we don't
            # mutate the caller's list.
            if request.enable_prompt_cache and tools_list:
                last = dict(tools_list[-1])
                last["cache_control"] = {"type": "ephemeral"}
                tools_list = tools_list[:-1] + [last]
            payload["tools"] = tools_list
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        return payload

    async def healthcheck(self) -> ProviderStatus:
        """Probe the messages endpoint with a minimal request.

        Anthropic does not expose a dedicated health endpoint; we send a tiny
        completion (1-token max, single 'ping' message) so the round-trip
        exercises auth + connectivity. Empty api_key short-circuits to
        unhealthy without firing the request.
        """
        if not self._api_key:
            self.status.healthy = False
            return self.status
        started = time.monotonic()
        try:
            payload = {
                "model": self._model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }
            async with self.build_async_client(timeout_s=self._timeout_s) as client:
                response = await client.post(
                    f"{self._base_url}/v1/messages",
                    json=payload,
                    headers=self._headers(stream=False),
                )
            elapsed_ms = (time.monotonic() - started) * 1000
            self.status.latency_ms = elapsed_ms
            self.status.avg_latency_ms = elapsed_ms
            self.status.healthy = 200 <= response.status_code < 300
        except (httpx.HTTPError, OSError):
            self.status.healthy = False
        return self.status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Execute a non-streaming Anthropic Messages request."""
        payload = self._request_payload(request, stream=False)

        async def _op() -> LlmResponse:
            async with self.build_async_client(timeout_s=self._timeout_s) as client:
                response = await client.post(
                    f"{self._base_url}/v1/messages",
                    json=payload,
                    headers=self._headers(stream=False),
                )
            response.raise_for_status()
            llm_response = normalize_anthropic_completion_payload(
                response.json(),
                provider_name=self.name,
                fallback_model=str(request.model or self._model),
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
            return llm_response

        handled_errors: tuple[type[BaseException], ...] = (httpx.HTTPError, ValueError)
        return await self.execute_with_telemetry(_op, handled_exceptions=handled_errors)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Execute a streaming Anthropic Messages request (SSE).

        Anthropic SSE events arrive as multi-line records separated by
        blank lines::

            event: content_block_delta
            data: {"type":"content_block_delta", ...}

        ``httpx.aiter_lines`` may or may not yield the blank separator
        depending on transport buffering, so we don't rely on it.
        Instead we accumulate ``event_name`` and ``data_payload`` from
        the lines we see; when a new ``event:`` line arrives, any complete
        previously-buffered pair is flushed. The same flush happens at
        stream end so the final ``message_delta`` event is not dropped.
        """
        payload = self._request_payload(request, stream=True)
        stream_request = StreamRequest(
            timeout_s=self._timeout_s,
            method="POST",
            url=f"{self._base_url}/v1/messages",
            headers=self._headers(stream=True),
            json=payload,
            handled_exceptions=(httpx.HTTPError, ValueError),
        )
        model_name = str(request.model or self._model)

        def _flush(
            event_name: str, data_payload: str
        ) -> LlmStreamEvent | None:
            if not event_name or not data_payload:
                return None
            try:
                data_obj = json.loads(data_payload)
            except json.JSONDecodeError:
                return None
            if not isinstance(data_obj, dict):
                return None
            return _stream_event_from_anthropic(
                event_name,
                data_obj,
                provider_name=self.name,
                model_name=model_name,
            )

        pending_event = ""
        pending_data = ""
        async with self.stream_client_with_telemetry(stream_request) as lines:
            async for line in lines:
                if not line:
                    event = _flush(pending_event, pending_data)
                    pending_event = ""
                    pending_data = ""
                    if event is None:
                        continue
                    yield event
                    if event.event == "done":
                        return
                    continue
                if line.startswith("event:"):
                    # New event starts — flush any complete previous pair first.
                    event = _flush(pending_event, pending_data)
                    pending_data = ""
                    if event is not None:
                        yield event
                        if event.event == "done":
                            return
                    pending_event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    pending_data = line[len("data:") :].strip()
            # End of stream — flush remaining.
            event = _flush(pending_event, pending_data)
            if event is not None:
                yield event


__all__ = [
    "AnthropicProvider",
    "normalize_anthropic_completion_payload",
    "_normalize_messages",
    "_stream_event_from_anthropic",
    "_parse_sse_event_line",
    "_map_stop_reason",
]
