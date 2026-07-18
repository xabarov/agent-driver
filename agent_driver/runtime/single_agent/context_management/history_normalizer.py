"""Reusable message-history normalization (epic 018, message-protocol hygiene).

Folding the tool-call protocol into plain user/assistant turns started life as the
last-resort forced-final retry (epic 016). Some providers/models handle the folded
view strictly better (deepseek-class empty finals; strict-alternation providers
400-ing on tool-role tails — reference: hermes ``moa_loop`` uses folding as the
STANDARD view for reference models, hermes ``message_sanitization`` closes
interrupted tool sequences). This module makes the transformations reusable so
retries, provider profiles and trimming integrity share one implementation.
"""

from __future__ import annotations

import json

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage


def fold_tool_history(messages: list[ChatMessage]) -> tuple[list[ChatMessage], bool]:
    """Fold tool exchanges into plain user/assistant turns, preserving evidence.

    Each ``tool``-role result becomes a USER message carrying the payload verbatim
    (``[Tool result: name]``); assistant tool-call markers keep only their text (or
    a ``(called tools: …)`` note). Returns ``(messages, changed)``; ``changed`` is
    False when the history carries no tool protocol at all.
    """
    folded: list[ChatMessage] = []
    changed = False
    for message in messages:
        if message.role == ChatRole.TOOL:
            changed = True
            folded.append(fold_tool_result_message(message))
            continue
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if message.role == ChatRole.ASSISTANT and metadata.get("tool_calls"):
            changed = True
            calls = metadata.get("tool_calls")
            names = ", ".join(
                str(
                    (
                        (call.get("function") or {}) if isinstance(call, dict) else {}
                    ).get("name")
                    or "tool"
                )
                for call in (calls if isinstance(calls, list) else [])
            )
            next_metadata = {k: v for k, v in metadata.items() if k != "tool_calls"}
            next_metadata["folded_tool_calls"] = True
            folded.append(
                ChatMessage(
                    role=ChatRole.ASSISTANT,
                    content=(message.content or "").strip()
                    or f"(called tools: {names})",
                    metadata=next_metadata,
                )
            )
            continue
        folded.append(message)
    return folded, changed


def fold_tool_result_message(message: ChatMessage) -> ChatMessage:
    """Convert one tool-role result into an information-preserving USER message."""
    tool_name = message.name or "tool"
    return ChatMessage(
        role=ChatRole.USER,
        content=f"[Tool result: {tool_name}]\n{message.content}",
        metadata={
            "folded_tool_result": True,
            "tool_call_id": message.tool_call_id,
        },
    )


def close_interrupted_tool_sequence(
    messages: list[ChatMessage],
) -> tuple[list[ChatMessage], bool]:
    """Close a history whose TAIL is an unanswered tool exchange (hermes pattern).

    Handles the pre-completion shape: a trailing ``assistant`` with ``tool_calls``
    and no results yet gets stub tool results for every expected call id, so the
    request does not violate the assistant(tool_calls) → tool ordering strict
    providers enforce. NB: a trailing raw ``tool`` result is the CANONICAL state
    right before a completion and must NOT be closed here — appending after a tool
    tail is the concern of :func:`close_tool_tail_before_user_injection` (steering/
    resume paths that inject a user message).
    """
    if not messages:
        return messages, False
    tail = messages[-1]
    metadata = tail.metadata if isinstance(tail.metadata, dict) else {}
    if tail.role == ChatRole.ASSISTANT and metadata.get("tool_calls"):
        closed = list(messages)
        calls = metadata.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            call_id = (
                str((call or {}).get("id") or "") if isinstance(call, dict) else ""
            )
            name = (
                str(((call.get("function") or {}) or {}).get("name") or "tool")
                if isinstance(call, dict)
                else "tool"
            )
            closed.append(
                ChatMessage(
                    role=ChatRole.TOOL,
                    name=name,
                    tool_call_id=call_id or None,
                    content=json.dumps(
                        {
                            "status": "interrupted",
                            "detail": "tool execution was interrupted before a result was produced",
                        }
                    ),
                    metadata={"interrupted_tool_stub": True},
                )
            )
        return closed, True
    return messages, False


def close_tool_tail_before_user_injection(
    messages: list[ChatMessage],
) -> tuple[list[ChatMessage], bool]:
    """Append a synthetic assistant ack when a USER message is about to follow a tool tail.

    ``tool → user`` breaks strict-alternation providers (Gemini/Claude — hermes
    ``close_interrupted_tool_sequence`` reference). Use on steering/resume paths that
    inject user content after an interrupted tool exchange; NOT part of the standard
    pre-completion validation (a trailing tool result there is canonical).
    """
    if not messages or messages[-1].role != ChatRole.TOOL:
        return messages, False
    closed = list(messages)
    closed.append(
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content="(processing tool results)",
            metadata={"interrupted_sequence_close": True},
        )
    )
    return closed, True


def repair_tool_call_arguments(
    messages: list[ChatMessage],
) -> tuple[list[ChatMessage], bool]:
    """Repair non-JSON tool-call argument strings in assistant messages.

    Local/small models occasionally emit truncated or single-quoted JSON in
    ``tool_calls[].function.arguments`` (hermes ``_repair_tool_call_arguments``).
    Strict providers reject the whole request on replay. Unparseable arguments are
    replaced with ``{}`` and flagged, so history stays sendable; the original raw
    string is preserved under ``raw_arguments`` for diagnostics.
    """
    repaired: list[ChatMessage] = []
    changed = False
    for message in messages:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        calls = metadata.get("tool_calls")
        if message.role != ChatRole.ASSISTANT or not isinstance(calls, list):
            repaired.append(message)
            continue
        new_calls = []
        message_changed = False
        for call in calls:
            if not isinstance(call, dict):
                new_calls.append(call)
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                new_calls.append(call)
                continue
            arguments = function.get("arguments")
            if not isinstance(arguments, str) or not arguments.strip():
                new_calls.append(call)
                continue
            try:
                json.loads(arguments)
            except json.JSONDecodeError:
                message_changed = True
                new_function = dict(function)
                new_function["arguments"] = "{}"
                new_function["raw_arguments"] = arguments[:2000]
                new_call = dict(call)
                new_call["function"] = new_function
                new_calls.append(new_call)
                continue
            new_calls.append(call)
        if message_changed:
            changed = True
            new_metadata = dict(metadata)
            new_metadata["tool_calls"] = new_calls
            new_metadata["tool_call_arguments_repaired"] = True
            repaired.append(message.model_copy(update={"metadata": new_metadata}))
        else:
            repaired.append(message)
    return repaired, changed


__all__ = [
    "close_interrupted_tool_sequence",
    "close_tool_tail_before_user_injection",
    "fold_tool_history",
    "fold_tool_result_message",
    "repair_tool_call_arguments",
]
