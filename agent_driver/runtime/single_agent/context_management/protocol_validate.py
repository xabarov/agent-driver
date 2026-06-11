"""Validate and repair chat protocol message sequences before LLM calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage


@dataclass(frozen=True, slots=True)
class ProtocolValidationResult:
    messages: tuple[ChatMessage, ...]
    repairs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def validate_and_repair_protocol_messages(
    messages: tuple[ChatMessage, ...] | list[ChatMessage],
    *,
    max_total_content_chars: int | None = None,
) -> ProtocolValidationResult:
    """Normalize message order and drop invalid rows for OpenAI-compatible APIs."""
    working = [
        (
            message
            if isinstance(message, ChatMessage)
            else ChatMessage.model_validate(message)
        )
        for message in messages
    ]
    repairs: list[str] = []
    warnings: list[str] = []
    normalized = _coalesce_adjacent_users(working, repairs)
    normalized = _drop_empty_assistants_without_tool_calls(normalized, repairs)
    normalized = _repair_tool_call_pairing(normalized, repairs, warnings)
    if max_total_content_chars is not None and max_total_content_chars > 0:
        normalized = _truncate_total_content(
            normalized,
            max_total_content_chars=max_total_content_chars,
            repairs=repairs,
        )
    return ProtocolValidationResult(
        messages=tuple(normalized),
        repairs=tuple(repairs),
        warnings=tuple(warnings),
    )


def _coalesce_adjacent_users(
    messages: list[ChatMessage], repairs: list[str]
) -> list[ChatMessage]:
    merged: list[ChatMessage] = []
    for message in messages:
        if (
            merged
            and merged[-1].role == ChatRole.USER
            and message.role == ChatRole.USER
        ):
            left = (merged[-1].content or "").strip()
            right = (message.content or "").strip()
            combined = "\n\n".join(part for part in [left, right] if part)
            merged[-1] = ChatMessage(role=ChatRole.USER, content=combined)
            repairs.append("coalesced_adjacent_user_messages")
            continue
        merged.append(message)
    return merged


def _drop_empty_assistants_without_tool_calls(
    messages: list[ChatMessage], repairs: list[str]
) -> list[ChatMessage]:
    kept: list[ChatMessage] = []
    for message in messages:
        if message.role != ChatRole.ASSISTANT:
            kept.append(message)
            continue
        if (message.content or "").strip():
            kept.append(message)
            continue
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if metadata.get("tool_calls"):
            kept.append(message)
            continue
        repairs.append("dropped_empty_assistant_without_tool_calls")
    return kept


def _repair_tool_call_pairing(
    messages: list[ChatMessage],
    repairs: list[str],
    warnings: list[str],
) -> list[ChatMessage]:
    kept: list[ChatMessage] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role != ChatRole.ASSISTANT:
            kept.append(message)
            index += 1
            continue
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        tool_calls = metadata.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            kept.append(message)
            index += 1
            continue
        expected_ids = [
            str(call.get("id")).strip()
            for call in tool_calls
            if isinstance(call, dict) and str(call.get("id") or "").strip()
        ]
        kept.append(message)
        index += 1
        seen_ids: set[str] = set()
        while index < len(messages) and messages[index].role == ChatRole.TOOL:
            tool_message = messages[index]
            call_id = str(tool_message.tool_call_id or "").strip()
            if call_id and call_id in expected_ids:
                seen_ids.add(call_id)
                kept.append(tool_message)
            else:
                repairs.append("dropped_orphan_tool_message")
            index += 1
        missing = [call_id for call_id in expected_ids if call_id not in seen_ids]
        if missing:
            warnings.append(f"missing_tool_results:{','.join(missing)}")
            for call_id in missing:
                kept.append(
                    ChatMessage(
                        role=ChatRole.TOOL,
                        name=_tool_name_for_call(tool_calls, call_id),
                        tool_call_id=call_id,
                        content=(
                            "[trimmed] Prior tool result was dropped due to "
                            "context budget. Re-run the tool if exact values are "
                            "needed."
                        ),
                        metadata={"tool_trim_stub": True},
                    )
                )
            repairs.append("inserted_missing_tool_result_stubs")
    return kept


def _tool_name_for_call(tool_calls: list[Any], call_id: str) -> str | None:
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        if str(call.get("id") or "").strip() != call_id:
            continue
        function = call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _truncate_total_content(
    messages: list[ChatMessage],
    *,
    max_total_content_chars: int,
    repairs: list[str],
) -> list[ChatMessage]:
    total = sum(len(message.content or "") for message in messages)
    if total <= max_total_content_chars:
        return messages
    trimmed: list[ChatMessage] = []
    running = 0
    for message in reversed(messages):
        content = message.content or ""
        if running + len(content) <= max_total_content_chars:
            trimmed.append(message)
            running += len(content)
            continue
        if message.role == ChatRole.TOOL:
            remaining = max(0, max_total_content_chars - running)
            stub = content[:remaining] if remaining else ""
            if len(content) > len(stub):
                repairs.append(f"truncated_tool_message:{message.name or 'tool'}")
            trimmed.append(
                ChatMessage(
                    role=message.role,
                    content=stub,
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                    metadata=message.metadata,
                )
            )
            running += len(stub)
            continue
        trimmed.append(message)
        running += len(content)
    trimmed.reverse()
    return trimmed


__all__ = ["ProtocolValidationResult", "validate_and_repair_protocol_messages"]
