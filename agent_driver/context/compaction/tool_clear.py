"""Idle/time-based clear-keep of old tool results (epic 035 phase B).

Port of openclaude ``microCompact.ts`` — the cheapest layer, LLM-free and binary:
when the idle gap since the last assistant turn exceeds a threshold (the server
prompt cache has expired, so the whole prefix will be rewritten anyway), clear the
CONTENT of old tool results, keeping the most recent ``keep_recent`` intact. The
cleared body is replaced with a fixed marker; the message and its tool_call_id
survive so the tool_use/tool_result pairing is intact.

Complementary to the tiered ``tool_history`` layer (A): A shrinks by size tier for a
stateless provider on every call; B clears wholesale on an idle-gap boundary. Both
are idempotent — an already-cleared result is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage

CLEARED_MARKER = "[Old tool result content cleared]"


@dataclass(frozen=True, slots=True)
class ToolClearResult:
    messages: list[ChatMessage]
    cleared: int
    chars_saved: int


def clear_old_tool_results(
    messages: list[ChatMessage],
    *,
    keep_recent: int = 3,
) -> ToolClearResult:
    """Clear content of tool results older than the ``keep_recent`` newest.

    ``keep_recent`` is floored at 1 (openclaude keeps at least the latest). Already
    cleared results are skipped (idempotent). The caller decides WHEN to run this
    (the idle-gap trigger) — this function is the pure clear/keep step.
    """
    keep = max(1, keep_recent)
    tool_indices = [i for i, m in enumerate(messages) if m.role == ChatRole.TOOL]
    if len(tool_indices) <= keep:
        return ToolClearResult(messages=messages, cleared=0, chars_saved=0)
    clear_indices = set(tool_indices[:-keep])
    result = list(messages)
    cleared = 0
    chars_saved = 0
    for idx in clear_indices:
        msg = result[idx]
        content = msg.content or ""
        if content.strip() == CLEARED_MARKER:
            continue  # idempotent
        chars_saved += max(0, len(content) - len(CLEARED_MARKER))
        result[idx] = msg.model_copy(update={"content": CLEARED_MARKER})
        cleared += 1
    return ToolClearResult(messages=result, cleared=cleared, chars_saved=chars_saved)


def idle_gap_exceeded(
    last_assistant_ts: float | None,
    now_ts: float,
    *,
    gap_threshold_seconds: float,
) -> bool:
    """True when the idle gap since the last assistant turn crosses the threshold.

    The trigger rationale (openclaude): past this gap the server cache has expired
    and the full prefix will be rewritten regardless — so clear old tool results
    now, before the request, to shrink what gets rewritten. Timestamps are passed in
    (the engine forbids wall-clock in pure helpers); ``None`` last-ts → not idle.
    """
    if last_assistant_ts is None or gap_threshold_seconds <= 0:
        return False
    return (now_ts - last_assistant_ts) >= gap_threshold_seconds


__all__ = [
    "CLEARED_MARKER",
    "ToolClearResult",
    "clear_old_tool_results",
    "idle_gap_exceeded",
]
