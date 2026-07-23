"""Request payload builders for OpenAI-compatible chat completions."""

from __future__ import annotations

from typing import Any

from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.tool_result_unpacker import build_openai_tool_content_list


def normalize_tool_choice_for_openai(
    value: str | dict[str, Any],
) -> str | dict[str, Any]:
    """Convert the SDK-neutral ``tool_choice`` payload to the OpenAI shape."""
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


def build_openai_completion_payload(
    request: LlmRequest,
    *,
    model: str,
    max_tokens_default: int | None,
    extra_body: dict[str, Any],
    stream: bool,
) -> dict[str, Any]:
    """Build an OpenAI-compatible chat/completions request payload."""
    messages_payload: list[dict[str, Any]] = []
    for message in request.messages:
        # When a message carries binary/url attachments in metadata, emit the
        # OpenAI ``content`` list (text + image_url blocks) instead of the flat
        # string. Originally tool-only (Phase 13 H29.2 — tool_stage screenshots);
        # now also for user/assistant messages so a user-supplied image reaches
        # a vision model end-to-end.
        attachments = message.metadata.get("attachments")
        content_blocks: list[dict[str, Any]] | None = None
        if isinstance(attachments, list) and attachments:
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
    payload: dict[str, Any] = {
        "model": request.model or model,
        "messages": messages_payload,
        "stream": stream,
    }
    max_tokens = (
        request.max_tokens if request.max_tokens is not None else max_tokens_default
    )
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.tools:
        payload["tools"] = request.tools
        payload["tool_choice"] = normalize_tool_choice_for_openai(
            request.tool_choice if request.tool_choice is not None else "auto"
        )
        # Phase 13 H29 — emit ``parallel_tool_calls`` only when the
        # caller explicitly set it. None means "use provider default"
        # (most backends are True), so omitting the key avoids
        # accidental opt-out on backends that default differently.
        if request.parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = request.parallel_tool_calls
    elif request.tool_choice is not None:
        payload["tool_choice"] = normalize_tool_choice_for_openai(request.tool_choice)
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
    for key, value in extra_body.items():
        payload[key] = value
    request_extra_body = request.metadata.get("provider_extra_body")
    if isinstance(request_extra_body, dict):
        for key, value in request_extra_body.items():
            payload[key] = value
    if request.enable_prompt_cache:
        apply_prompt_cache_markers(messages_payload)
    return payload


# Epic 028, hermes system_and_3: Anthropic-family models behind OpenRouter honor
# explicit cache_control breakpoints (max 4); OpenAI/DeepSeek families cache
# implicitly and are documented to ignore the field. Placement quirks that cost
# real money if ignored (hermes prompt_caching.py, live-verified on OpenRouter):
# - a marker on an EMPTY-content message (assistant pure-tool_calls, empty tool
#   result) is silently ignored -> one of the four breakpoints wasted;
# - a top-level marker on ``role:tool`` can HANG the request -> tool rows are
#   never carriers at all;
# - list-content carries the marker on its LAST content part, not top-level.
_CACHE_MARKER = {"type": "ephemeral"}
_MAX_MESSAGE_MARKERS = 3


def _can_carry_cache_marker(row: dict[str, Any]) -> bool:
    """Whether this payload row is a safe, non-wasteful cache_control carrier."""
    if str(row.get("role", "")) in {"tool", "system"}:
        return False
    content = row.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(part, dict)
            and part.get("type") == "text"
            and str(part.get("text") or "").strip()
            for part in content
        )
    return False


def _mark_row(row: dict[str, Any]) -> None:
    content = row.get("content")
    if isinstance(content, str):
        row["content"] = [
            {"type": "text", "text": content, "cache_control": dict(_CACHE_MARKER)}
        ]
        return
    if isinstance(content, list):
        for part in reversed(content):
            if isinstance(part, dict) and part.get("type") == "text":
                part["cache_control"] = dict(_CACHE_MARKER)
                return


def apply_prompt_cache_markers(messages_payload: list[dict[str, Any]]) -> None:
    """Place system + last-3 cache breakpoints (hermes ``system_and_3``)."""
    for row in messages_payload:
        if (
            str(row.get("role", "")) == "system"
            and isinstance(row.get("content"), str)
            and str(row.get("content") or "").strip()
        ):
            row["content"] = [
                {
                    "type": "text",
                    "text": row["content"],
                    "cache_control": dict(_CACHE_MARKER),
                }
            ]
            break
    marked = 0
    for row in reversed(messages_payload):
        if marked >= _MAX_MESSAGE_MARKERS:
            break
        if not _can_carry_cache_marker(row):
            continue
        _mark_row(row)
        marked += 1


__all__ = [
    "apply_prompt_cache_markers",
    "build_openai_completion_payload",
    "normalize_tool_choice_for_openai",
]
