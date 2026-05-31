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
            "content": content_blocks if content_blocks is not None else message.content,
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
        request.max_tokens
        if request.max_tokens is not None
        else max_tokens_default
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
        payload["tool_choice"] = normalize_tool_choice_for_openai(
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
    for key, value in extra_body.items():
        payload[key] = value
    request_extra_body = request.metadata.get("provider_extra_body")
    if isinstance(request_extra_body, dict):
        for key, value in request_extra_body.items():
            payload[key] = value
    return payload


__all__ = ["build_openai_completion_payload", "normalize_tool_choice_for_openai"]
