"""Session-memory compaction deterministic path tests."""

from __future__ import annotations

from agent_driver.context.compaction import build_session_memory_compaction
from agent_driver.contracts import SessionMemory


def test_session_memory_compaction_keeps_bounded_tail_and_refs() -> None:
    """Session-memory compaction should preserve refs and bounded recent tail."""
    memory = SessionMemory(
        memory_id="mem_1",
        session_id="session_1",
        summary="Long work summary",
        key_facts=["a", "b"],
        pending_tasks=["todo"],
        open_questions=[],
        last_summarized_turn_index=9,
    )
    compacted = build_session_memory_compaction(
        session_memory=memory,
        recent_tail_messages=[
            {"role": "user", "content": f"msg {idx}"} for idx in range(10)
        ],
        planning_state={"next_plan": "continue"},
        retained_digest_ids=["dig_1", "dig_2"],
        retained_artifact_ids=["art_1"],
        recent_tail_limit=3,
    )
    assert len(compacted.prompt_messages) == 5
    assert compacted.retained_digest_ids == ["dig_1", "dig_2"]
    assert compacted.retained_artifact_ids == ["art_1"]
