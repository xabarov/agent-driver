"""E5: deterministic tool-call argument truncation pre-pass.

A cheap, LLM-free mitigation that fires before full summarization: oversized
string arguments of tool calls in *older* messages (e.g. a file-write's whole
``content`` arg) are clipped to a head + marker, shrinking the tokens the
provider re-sends each turn. Mirrors deepagents' ``truncate_args_settings`` —
a lightweight first stage that often defers (or avoids) the expensive LLM
compaction. Pure and deterministic: returns new messages plus an audit; never
mutates the input. Tool calls live in ``message.metadata["tool_calls"]`` as a
list of ``{tool_name, args: {...}, ...}`` dicts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_driver.contracts.messages import ChatMessage

_TRUNCATION_MARKER = " …[arg truncated: {dropped} chars]"


@dataclass(slots=True)
class ToolArgTruncationResult:
    """Outcome of the pre-pass: new messages + what was clipped."""

    messages: list[ChatMessage]
    audit: list[dict[str, Any]] = field(default_factory=list)
    retained_structured: list[dict[str, Any]] = field(default_factory=list)
    chars_saved: int = 0

    @property
    def changed(self) -> bool:
        """True when at least one argument was clipped."""
        return bool(self.audit)


def _truncate_value(value: str, max_chars: int) -> tuple[str, int]:
    """Clip a string to ``max_chars`` head + a marker; return (new, dropped)."""
    if len(value) <= max_chars:
        return value, 0
    dropped = len(value) - max_chars
    return value[:max_chars] + _TRUNCATION_MARKER.format(dropped=dropped), dropped


def truncate_tool_call_args(
    messages: list[ChatMessage],
    *,
    max_arg_chars: int = 2000,
    protect_last: int = 2,
) -> ToolArgTruncationResult:
    """Clip oversized tool-call string args in all but the last ``protect_last``.

    Only string argument values longer than ``max_arg_chars`` are clipped; the
    most recent ``protect_last`` messages are left untouched (the live tail the
    model is actively working with). Each clip is recorded in the audit.
    """
    if max_arg_chars < 0:
        raise ValueError("max_arg_chars must be >= 0")
    cutoff = max(0, len(messages) - max(0, protect_last))
    audit: list[dict[str, Any]] = []
    retained_structured: list[dict[str, Any]] = []
    saved = 0
    out: list[ChatMessage] = []
    for index, message in enumerate(messages):
        if index >= cutoff:
            out.append(message)
            continue
        tool_calls = message.metadata.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            out.append(message)
            continue
        new_calls, msg_saved, msg_audit, msg_retained = _truncate_calls(
            tool_calls, index=index, max_arg_chars=max_arg_chars
        )
        retained_structured.extend(msg_retained)
        if not msg_audit:
            out.append(message)
            continue
        saved += msg_saved
        audit.extend(msg_audit)
        out.append(
            message.model_copy(
                update={"metadata": {**message.metadata, "tool_calls": new_calls}}
            )
        )
    return ToolArgTruncationResult(
        messages=out,
        audit=audit,
        chars_saved=saved,
        retained_structured=retained_structured,
    )


def _truncate_calls(
    tool_calls: list[Any], *, index: int, max_arg_chars: int
) -> tuple[list[Any], int, list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (new_calls, chars_saved, audit) for one message's tool_calls."""
    new_calls: list[Any] = []
    saved = 0
    audit: list[dict[str, Any]] = []
    retained_structured: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict) or not isinstance(call.get("args"), dict):
            new_calls.append(call)
            continue
        new_args = dict(call["args"])
        changed = False
        for key, value in list(new_args.items()):
            structured_size = _structured_size(value)
            if structured_size is not None and structured_size > max_arg_chars:
                retained_structured.append(
                    {
                        "message_index": index,
                        "tool_name": str(call.get("tool_name", "") or ""),
                        "arg": key,
                        "original_chars": structured_size,
                        "strategy": "structured_json_retained_atomically",
                    }
                )
                continue
            if not isinstance(value, str):
                continue
            clipped, dropped = _truncate_value(value, max_arg_chars)
            if dropped:
                new_args[key] = clipped
                saved += dropped
                changed = True
                audit.append(
                    {
                        "message_index": index,
                        "tool_name": str(call.get("tool_name", "") or ""),
                        "arg": key,
                        "dropped_chars": dropped,
                    }
                )
        new_calls.append({**call, "args": new_args} if changed else call)
    return new_calls, saved, audit, retained_structured


def _structured_size(value: Any) -> int | None:
    """Return JSON size for structured values, including serialized objects/arrays."""
    parsed = value
    if isinstance(value, str):
        if value.lstrip()[:1] not in ("{", "["):
            return None
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(parsed, (dict, list)):
        return None
    try:
        return len(
            json.dumps(
                parsed,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return None


__all__ = ["ToolArgTruncationResult", "truncate_tool_call_args"]
