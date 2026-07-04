"""Finalize-time recovery of a degenerate terminal answer (epic 015, Phase C).

When a ReAct loop over-iterates on a pure-text run — the model produces a complete answer, then
re-answers without any tool call and finalizes a degenerate «task already done / see previous answer»
stub (or an empty turn) — the last assistant turn becomes the run answer and the real answer is lost.

This module recovers the substantive answer the model already produced, from the run's
``assistant_message_completed`` event log. It is a raw-free, language-agnostic safety net:

* Gated on ``tool_call_count == 0`` — a tool-informed run's terminal answer is trusted and never
  overridden (the last turn is a legitimate post-tool synthesis, not a no-progress restatement).
* Only triggers when the terminal answer is empty, or a short restatement much shorter than an
  earlier substantive turn of the same run.

The detector is deterministic and pure so it can be unit-tested without a live loop; the streaming
over-iteration that produces the degenerate finalize is exercised separately (epic 015 Phase A/D).
"""

from __future__ import annotations

from typing import Any

# A turn long enough to be a real answer rather than a hedge/stub.
SUBSTANTIVE_ANSWER_CHARS = 400
# A terminal answer this short, with a much longer earlier substantive turn, reads as a degenerate
# restatement of an answer the loop already produced.
SHORT_TERMINAL_ANSWER_CHARS = 200
# The earlier substantive turn must dwarf the short terminal to count as a discarded real answer.
MIN_SUBSTANTIVE_RATIO = 4

_ASSISTANT_COMPLETED_EVENTS = {
    "assistant_message_completed",
    "assistant_message_replaced",
}


def _event_name(event: Any) -> str:
    # RuntimeEvent stores the name in ``.type`` (a RuntimeEventType enum); event-log dicts and adapter
    # projections use ``event``/``type`` string keys. Normalise all of them, unwrapping enum ``.value``.
    if isinstance(event, dict):
        name: Any = event.get("event") or event.get("type") or ""
    else:
        name = getattr(event, "type", None) or getattr(event, "event", "") or ""
    return str(getattr(name, "value", name) or "").strip()


def _event_payload(event: Any) -> dict:
    payload = (
        event.get("payload")
        if isinstance(event, dict)
        else getattr(event, "payload", None)
    )
    return payload if isinstance(payload, dict) else {}


def assistant_turn_contents(events: Any) -> list[str]:
    """Ordered assistant-turn contents from the run's event log (empty turns included)."""
    contents: list[str] = []
    for event in events or []:
        if _event_name(event) in _ASSISTANT_COMPLETED_EVENTS:
            contents.append(str(_event_payload(event).get("content") or ""))
    return contents


def recover_degenerate_terminal_answer(
    *,
    events: Any,
    terminal_answer: str | None,
    tool_call_count: int,
) -> tuple[str | None, str | None]:
    """Return ``(recovered_answer, reason)`` or ``(None, None)``.

    ``reason`` is ``empty_terminal_answer`` or ``degenerate_short_restatement``. A ``None`` recovery
    means the terminal answer stands (the common case — this is a no-op for well-behaved runs).
    """
    if tool_call_count and tool_call_count > 0:
        return None, None
    substantive = [
        c
        for c in assistant_turn_contents(events)
        if len(c.strip()) >= SUBSTANTIVE_ANSWER_CHARS
    ]
    if not substantive:
        return None, None
    best = max(substantive, key=lambda c: len(c.strip()))
    term = (terminal_answer or "").strip()
    if term == best.strip():
        return None, None
    if not term:
        return best, "empty_terminal_answer"
    if len(term) < SHORT_TERMINAL_ANSWER_CHARS and len(
        best.strip()
    ) >= MIN_SUBSTANTIVE_RATIO * max(1, len(term)):
        return best, "degenerate_short_restatement"
    return None, None


__all__ = [
    "assistant_turn_contents",
    "recover_degenerate_terminal_answer",
    "SUBSTANTIVE_ANSWER_CHARS",
    "SHORT_TERMINAL_ANSWER_CHARS",
]
