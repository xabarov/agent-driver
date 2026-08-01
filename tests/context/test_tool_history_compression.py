"""Epic 035 A: tiered compression of old tool-result bulk."""

from __future__ import annotations

from agent_driver.context.compaction.tool_history import (
    MID_MAX_CHARS,
    compress_tool_history,
    get_tiers,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage


def _tool(text: str, cid: str) -> ChatMessage:
    return ChatMessage(role=ChatRole.TOOL, content=text, tool_call_id=cid)


def _user(text: str) -> ChatMessage:
    return ChatMessage(role=ChatRole.USER, content=text)


def test_json_tool_result_in_mid_tier_stays_valid_json() -> None:
    import json

    # A JSON-returning tool result aged into the mid tier must NOT be cut mid-JSON.
    big = json.dumps(
        {"status": "ok", "rows": [{"id": i, "blob": "Z" * 400} for i in range(20)]},
        ensure_ascii=True,
    )
    msgs = [_user("q")]
    for i in range(8):
        msgs.append(_tool(big, f"c{i}"))
    out, audit = compress_tool_history(msgs, effective_window=12_000)
    tool_msgs = [m for m in out if m.role == ChatRole.TOOL]
    # The mid-tier (truncated) results must still parse as JSON — structure intact.
    mid = tool_msgs[-3]  # a truncated (not stubbed) mid-tier result
    assert len(mid.content) < len(big)
    parsed = json.loads(mid.content)  # would raise if cut mid-structure
    assert parsed["status"] == "ok"
    assert len(parsed["rows"]) == 20  # shape preserved (all rows kept, blobs shrunk)
    assert audit["truncated"] == 3


def test_tiers_scale_with_window() -> None:
    assert get_tiers(12_000).recent == 2
    assert get_tiers(200_000).recent == 8
    assert get_tiers(1_000_000).recent == 25


def test_recent_full_mid_truncated_old_stubbed() -> None:
    # 8 tool results, small window (12k → recent=2, mid=3, rest stubbed).
    msgs = [_user("q")]
    for i in range(8):
        msgs.append(_tool("X" * 5000, f"c{i}"))  # oldest first
    out, audit = compress_tool_history(msgs, effective_window=12_000)

    tool_msgs = [m for m in out if m.role == ChatRole.TOOL]
    # newest 2 (indices 6,7) full; next 3 (3,4,5) truncated; oldest 3 (0,1,2) stubbed.
    assert tool_msgs[-1].content == "X" * 5000  # newest full
    assert tool_msgs[-2].content == "X" * 5000
    assert "truncated" in tool_msgs[-3].content and len(tool_msgs[-3].content) < 5000
    assert tool_msgs[0].content.startswith("[tool result → 5000 chars omitted]")
    assert audit["activated"] is True
    assert audit["truncated"] == 3
    assert audit["stubbed"] == 3
    assert audit["chars_saved"] > 0


def test_structure_preserved_no_message_lost() -> None:
    msgs = [_user("q"), _tool("A" * 9000, "c0"), _user("q2"), _tool("B" * 9000, "c1")]
    out, _audit = compress_tool_history(msgs, effective_window=12_000)
    assert len(out) == len(msgs)
    assert [m.role for m in out] == [m.role for m in msgs]


def test_idempotent_stub_terminal() -> None:
    msgs = [_user("q")]
    for i in range(8):
        msgs.append(_tool("X" * 5000, f"c{i}"))
    once, _ = compress_tool_history(msgs, effective_window=12_000)
    twice, audit2 = compress_tool_history(once, effective_window=12_000)
    # Second pass changes nothing (stubs terminal, truncated stays truncated).
    assert [m.content for m in twice] == [m.content for m in once]
    assert audit2["truncated"] == 0 and audit2["stubbed"] == 0


def test_no_tool_results_inert() -> None:
    msgs = [_user("q"), ChatMessage(role=ChatRole.ASSISTANT, content="a")]
    out, audit = compress_tool_history(msgs, effective_window=12_000)
    assert out is msgs
    assert audit["activated"] is False


def test_small_result_not_truncated() -> None:
    # Mid-tier but under MID_MAX_CHARS → left alone.
    msgs = [_user("q")]
    for i in range(5):
        msgs.append(_tool("s" * (MID_MAX_CHARS - 100), f"c{i}"))
    out, audit = compress_tool_history(msgs, effective_window=12_000)
    # recent=2 full; mid=3 but all under cap → not truncated; none old here (5 total).
    assert audit["truncated"] == 0
