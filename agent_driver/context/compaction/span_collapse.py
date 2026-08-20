"""Span selection + collapse for whole-turn-range compaction (epic 035 phase C).

Port of openclaude ``contextCollapse/spanSelection.ts`` primitives adapted to our
flat ``ChatMessage`` list. The most expensive compaction layer: it collapses ONE
oldest range of whole turns into a single summary placeholder, executed by a fork /
aux agent. This module owns the PURE, deterministic parts — span selection (protect
the first turn's framing and the recent working-set tail, anchor on turn starts so a
tool_use/tool_result pair is never split, size the span to drop under a target
window fraction), risk scoring, and placeholder construction. The summarization call
(fork/aux) and the runtime staged→committed wiring live at the caller; keeping the
selection pure makes it testable without a provider.

Constants verbatim from the reference:
* ``COLLAPSE_TARGET_RATIO = 0.7`` — collapse until projected total drops under this.
* ``PROTECTED_TAIL_RATIO = 0.3`` — most-recent fraction never collapsed (working set).
* ``MIN_COLLAPSE_TOKENS = 2000`` — below this a span isn't worth a model call.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.context.token_estimation import estimate_tokens
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage

COLLAPSE_TARGET_RATIO = 0.7
PROTECTED_TAIL_RATIO = 0.3
MIN_COLLAPSE_TOKENS = 2000


def _msg_tokens(msg: ChatMessage) -> int:
    return estimate_tokens(len(msg.content or ""))


def is_turn_start(msg: ChatMessage) -> bool:
    """A turn starts at a real user message (not a tool result carried as user)."""
    return msg.role == ChatRole.USER


def compute_risk(
    start_index: int, total_messages: int, span_tokens: int, effective_window: int
) -> float:
    """Drain-priority in [0,1]: blends span age (older→higher) and size (bigger→higher)."""
    age = 1 - start_index / total_messages if total_messages > 0 else 0.0
    size = min(span_tokens / effective_window, 1.0) if effective_window > 0 else 0.0
    return max(0.0, min(1.0, 0.5 * age + 0.5 * size))


@dataclass(frozen=True, slots=True)
class CollapseSpan:
    start: int  # inclusive
    end: int  # exclusive
    span_tokens: int
    risk: float


def select_collapse_span(
    messages: list[ChatMessage],
    *,
    effective_window: int,
    protected_tail_ratio: float = PROTECTED_TAIL_RATIO,
    target_ratio: float = COLLAPSE_TARGET_RATIO,
    min_collapse_tokens: int = MIN_COLLAPSE_TOKENS,
) -> CollapseSpan | None:
    """Pick the oldest collapsible turn-span, or ``None`` when none qualifies.

    Protects the first turn (task framing) and the most-recent ``protected_tail_ratio``
    of the window (the working set). Both boundaries anchor on turn starts so a
    tool_use/tool_result pair is never split. Grows the span turn-by-turn until the
    projected post-collapse total drops under ``target_ratio`` of the window. Returns
    ``None`` when there is no candidate or the span is below ``min_collapse_tokens``.
    """
    total = len(messages)
    if total < 3 or effective_window <= 0:
        return None
    token_prefix = [0]
    for msg in messages:
        token_prefix.append(token_prefix[-1] + _msg_tokens(msg))
    total_tokens = token_prefix[-1]

    # Protected tail: the last protected_tail_ratio of the window, anchored to a turn
    # start so we never collapse into a partial pair.
    protected_tail_tokens = int(effective_window * protected_tail_ratio)
    tail_start = total
    acc = 0
    for i in range(total - 1, -1, -1):
        acc = total_tokens - token_prefix[i]
        if acc >= protected_tail_tokens and is_turn_start(messages[i]):
            tail_start = i
            break
    else:
        tail_start = 0

    # Span start: after the first turn's framing (first turn start = index of first
    # user msg; protect that whole first turn up to the next turn start).
    first_turn_start = next((i for i, m in enumerate(messages) if is_turn_start(m)), 0)
    span_start = next(
        (i for i in range(first_turn_start + 1, total) if is_turn_start(messages[i])),
        first_turn_start,
    )
    if span_start >= tail_start:
        return None

    # Grow the span end (turn-start aligned) until projected total < target.
    target_tokens = int(effective_window * target_ratio)
    turn_starts = [
        i
        for i in range(span_start, tail_start + 1)
        if i == tail_start or is_turn_start(messages[i])
    ]
    if tail_start not in turn_starts:
        turn_starts.append(tail_start)
    span_end = span_start
    for boundary in turn_starts:
        if boundary <= span_start:
            continue
        span_end = boundary
        span_tokens = token_prefix[span_end] - token_prefix[span_start]
        projected = total_tokens - span_tokens  # summary cost ignored (upper bound)
        if projected < target_tokens:
            break
    span_tokens = token_prefix[span_end] - token_prefix[span_start]
    if span_tokens < min_collapse_tokens or span_end <= span_start:
        return None
    return CollapseSpan(
        start=span_start,
        end=span_end,
        span_tokens=span_tokens,
        risk=compute_risk(span_start, total, span_tokens, effective_window),
    )


def build_collapse_placeholder(collapse_id: str, summary: str) -> ChatMessage:
    """Wrap a span summary as a single system placeholder message."""
    return ChatMessage(
        role=ChatRole.SYSTEM,
        content=f'<collapsed id="{collapse_id}">{summary}</collapsed>',
        metadata={"is_collapse_summary": True, "collapse_id": collapse_id},
    )


def apply_collapse(
    messages: list[ChatMessage], span: CollapseSpan, placeholder: ChatMessage
) -> list[ChatMessage]:
    """Replace ``messages[span.start:span.end]`` with the summary placeholder."""
    return [*messages[: span.start], placeholder, *messages[span.end :]]


__all__ = [
    "COLLAPSE_TARGET_RATIO",
    "PROTECTED_TAIL_RATIO",
    "MIN_COLLAPSE_TOKENS",
    "CollapseSpan",
    "apply_collapse",
    "build_collapse_placeholder",
    "compute_risk",
    "is_turn_start",
    "select_collapse_span",
]
