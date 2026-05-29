"""Redacted LLM request payload diagnostics for provider failures."""

from __future__ import annotations

import json
import os
from typing import Any

from agent_driver.llm.contracts import LlmRequest


def debug_llm_payload_enabled() -> bool:
    return os.environ.get("AGENT_DRIVER_DEBUG_LLM_PAYLOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def summarize_llm_request_payload(request: LlmRequest) -> dict[str, Any]:
    """Build safe request stats without secrets or full tool bodies."""
    messages = request.messages if isinstance(request.messages, list) else []
    role_chars: dict[str, int] = {}
    tool_call_ids: list[str] = []
    for message in messages:
        role = message.role.value if hasattr(message.role, "value") else str(message.role)
        role_chars[role] = role_chars.get(role, 0) + len(message.content or "")
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        tool_calls = metadata.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id.strip():
                    tool_call_ids.append(call_id)
        if message.tool_call_id:
            tool_call_ids.append(str(message.tool_call_id))
    tools = request.tools if isinstance(request.tools, list) else []
    tool_names = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function_payload = tool.get("function")
        if isinstance(function_payload, dict):
            name = function_payload.get("name")
            if isinstance(name, str) and name.strip():
                tool_names.append(name)
    return {
        "model": request.model,
        "message_count": len(messages),
        "role_char_counts": role_chars,
        "total_content_chars": sum(role_chars.values()),
        "tool_call_ids": tool_call_ids[-20:],
        "tool_names": tool_names,
        "tool_choice": request.tool_choice,
        "stream": request.stream,
    }


def format_payload_debug_line(request: LlmRequest) -> str:
    return json.dumps(summarize_llm_request_payload(request), ensure_ascii=True)


__all__ = [
    "debug_llm_payload_enabled",
    "format_payload_debug_line",
    "summarize_llm_request_payload",
]
