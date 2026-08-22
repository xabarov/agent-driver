"""opencode-adoption EPIC-10 — emergency payload strip on context-overflow retry.

Pins ``emergency_strip_oversized_payloads``: wholesale-clear OLD tool results (keeping the
newest), hard-cap any oversized message (embedded blob / media) to its head + a marker,
preserve small messages + tool_call_id pairing, and stay idempotent.
"""

from __future__ import annotations

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.context_management.context_window_recovery import (
    emergency_strip_oversized_payloads,
)


def _msgs() -> list[ChatMessage]:
    return [
        ChatMessage(role=ChatRole.USER, content="small user turn"),
        ChatMessage(role=ChatRole.TOOL, content="A" * 8000, tool_call_id="c1"),
        ChatMessage(role=ChatRole.TOOL, content="B" * 8000, tool_call_id="c2"),
        ChatMessage(role=ChatRole.USER, content="Z" * 50_000),  # embedded blob / media
    ]


def test_clears_old_tool_results_keeping_recent() -> None:
    out, audit = emergency_strip_oversized_payloads(
        _msgs(), keep_recent_tool_results=1, max_message_chars=20_000
    )
    # oldest tool result cleared, newest kept intact
    assert "cleared" in out[1].content
    assert out[2].content == "B" * 8000
    # tool_call_id pairing preserved
    assert out[1].tool_call_id == "c1"
    assert audit["cleared"] == 1


def test_hard_caps_oversized_message() -> None:
    out, audit = emergency_strip_oversized_payloads(_msgs(), max_message_chars=20_000)
    big = out[3].content
    assert "chars dropped — context overflow]" in big
    assert len(big) < 21_000  # ~20k head + marker, not the original 50k
    assert audit["truncated"] == 1
    assert audit["chars_saved"] > 25_000


def test_small_messages_untouched() -> None:
    out, _ = emergency_strip_oversized_payloads(_msgs(), max_message_chars=20_000)
    assert out[0].content == "small user turn"


def test_idempotent_second_pass_is_noop() -> None:
    out1, _ = emergency_strip_oversized_payloads(
        _msgs(), keep_recent_tool_results=1, max_message_chars=20_000
    )
    out2, audit2 = emergency_strip_oversized_payloads(
        out1, keep_recent_tool_results=1, max_message_chars=20_000
    )
    assert audit2["cleared"] == 0
    assert audit2["truncated"] == 0
    assert [m.content for m in out2] == [m.content for m in out1]


def test_keep_zero_clears_all_tool_results() -> None:
    out, audit = emergency_strip_oversized_payloads(
        _msgs(), keep_recent_tool_results=0, max_message_chars=20_000
    )
    assert "cleared" in out[1].content and "cleared" in out[2].content
    assert audit["cleared"] == 2
