"""Epic 035 B/C: idle clear-keep layer + span-collapse selection primitives."""

from __future__ import annotations

from agent_driver.context.compaction.span_collapse import (
    MIN_COLLAPSE_TOKENS,
    apply_collapse,
    build_collapse_placeholder,
    compute_risk,
    is_turn_start,
    select_collapse_span,
)
from agent_driver.context.compaction.tool_clear import (
    CLEARED_MARKER,
    clear_old_tool_results,
    idle_gap_exceeded,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage


def _tool(text: str, cid: str) -> ChatMessage:
    return ChatMessage(role=ChatRole.TOOL, content=text, tool_call_id=cid)


def _user(text: str) -> ChatMessage:
    return ChatMessage(role=ChatRole.USER, content=text)


def _asst(text: str) -> ChatMessage:
    return ChatMessage(role=ChatRole.ASSISTANT, content=text)


# ---- B: idle clear-keep ----------------------------------------------------


def test_clear_keeps_recent_clears_old() -> None:
    msgs = [_user("q")] + [_tool("X" * 4000, f"c{i}") for i in range(5)]
    res = clear_old_tool_results(msgs, keep_recent=2)
    tool_msgs = [m for m in res.messages if m.role == ChatRole.TOOL]
    assert tool_msgs[-1].content == "X" * 4000  # newest kept
    assert tool_msgs[-2].content == "X" * 4000
    assert all(m.content == CLEARED_MARKER for m in tool_msgs[:-2])
    assert res.cleared == 3
    assert res.chars_saved > 0


def test_clear_idempotent_and_floor() -> None:
    msgs = [_tool("a" * 2000, "c0"), _tool("b" * 2000, "c1")]
    once = clear_old_tool_results(msgs, keep_recent=1)
    twice = clear_old_tool_results(once.messages, keep_recent=1)
    assert twice.cleared == 0  # already cleared
    # floor: keep_recent=0 still keeps 1.
    res0 = clear_old_tool_results(msgs, keep_recent=0)
    assert res0.cleared == 1


def test_idle_gap_trigger() -> None:
    assert idle_gap_exceeded(1000.0, 1000.0 + 400, gap_threshold_seconds=300) is True
    assert idle_gap_exceeded(1000.0, 1000.0 + 100, gap_threshold_seconds=300) is False
    assert idle_gap_exceeded(None, 5000.0, gap_threshold_seconds=300) is False


# ---- C: span-collapse selection --------------------------------------------


def test_risk_blends_age_and_size() -> None:
    old_big = compute_risk(0, 100, 8000, 16000)
    new_small = compute_risk(90, 100, 200, 16000)
    assert old_big > new_small


def test_select_protects_first_turn_and_tail() -> None:
    # Build a long conversation: first turn framing + many middle turns + recent tail.
    msgs = [_user("FIRST framing turn"), _asst("ok")]
    for i in range(20):
        msgs.append(_user(f"turn {i} " + "m" * 3000))
        msgs.append(_asst("a" * 3000))
    span = select_collapse_span(msgs, effective_window=16000)
    assert span is not None
    # First turn (index 0 user) is protected — span never starts at 0.
    assert span.start > 0
    assert is_turn_start(msgs[span.start])
    # Tail protected: span end is before the last few turns.
    assert span.end < len(msgs)
    assert span.span_tokens >= MIN_COLLAPSE_TOKENS


def test_select_none_when_too_short() -> None:
    assert (
        select_collapse_span([_user("q"), _asst("a")], effective_window=16000) is None
    )


def test_apply_collapse_replaces_span_with_placeholder() -> None:
    msgs = [
        _user("first"),
        _user("mid1"),
        _asst("a1"),
        _user("mid2"),
        _asst("a2"),
        _user("recent"),
    ]
    from agent_driver.context.compaction.span_collapse import CollapseSpan

    span = CollapseSpan(start=1, end=5, span_tokens=3000, risk=0.6)
    placeholder = build_collapse_placeholder("cx1", "Обсудили mid1/mid2, решений нет.")
    out = apply_collapse(msgs, span, placeholder)
    assert len(out) == 3  # first, placeholder, recent
    assert out[1].metadata.get("is_collapse_summary") is True
    assert "collapsed" in out[1].content
    assert out[0].content == "first" and out[2].content == "recent"
