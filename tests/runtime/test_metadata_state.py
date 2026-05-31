"""Tests for compatibility-preserving runtime metadata views."""

from __future__ import annotations

from agent_driver.runtime.metadata_state import (
    CompactionRuntimeState,
    LoopControlState,
    PlanningRuntimeState,
    ResearchRuntimeState,
    StreamingRuntimeState,
    ToolLoopState,
)


def test_planning_state_preserves_existing_metadata_keys() -> None:
    metadata = {"planning_state_seed": {"todos": []}}
    state = PlanningRuntimeState(metadata)

    assert state.pop_seed() == {"todos": []}
    state.set_planning_state({"todos": []})
    state.set_planning_step({"step_id": "plan_1"})
    state.set_last_todo_write_signature("sig")
    state.mark_todo_deduped()

    assert metadata == {
        "planning_state": {"todos": []},
        "planning_step": {"step_id": "plan_1"},
        "last_todo_write_signature": "sig",
        "todo_write_deduped": True,
    }


def test_streaming_state_preserves_streaming_lifecycle_shape() -> None:
    metadata: dict[str, object] = {}
    state = StreamingRuntimeState(metadata)

    state.mark_started()
    state.set_content("hello")
    state.mark_recovered(content="hello", reason="partial_stream")

    assert metadata["assistant_stream_started"] is True
    assert metadata["assistant_stream_completed"] is True
    assert metadata["assistant_stream_content"] == "hello"
    assert metadata["assistant_stream_recovered"] is True
    assert metadata["assistant_stream_recovery_reason"] == "partial_stream"


def test_tool_loop_and_research_state_use_legacy_keys() -> None:
    metadata: dict[str, object] = {
        "tool_results": [{"tool_name": "web_fetch"}],
        "tool_trace": [{"tool_name": "web_fetch"}],
    }

    assert ToolLoopState(metadata).tool_results() == [{"tool_name": "web_fetch"}]
    assert ToolLoopState(metadata).tool_trace() == [{"tool_name": "web_fetch"}]
    ToolLoopState(metadata).force_final_answer(reason="done")
    ResearchRuntimeState(metadata).set_contract(
        payload={"final_readiness": {"status": "allowed"}},
        status="allowed",
        reasons=[],
    )

    assert metadata["force_final_answer"] is True
    assert metadata["tool_choice_override"] == "none"
    assert metadata["force_final_answer_reason"] == "done"
    assert metadata["research_session_contract"] == {
        "final_readiness": {"status": "allowed"}
    }
    assert metadata["final_readiness"] == "allowed"
    assert metadata["repair_required_reasons"] == []


def test_loop_and_compaction_state_preserve_output_shapes() -> None:
    metadata: dict[str, object] = {
        "token_pressure": {"state": "ok"},
        "retained_digest_ids": ["dig_1"],
    }

    loop = LoopControlState(metadata)
    loop.next_step = "llm_call"
    loop.step_count = 2
    loop.set_terminal_output({"answer": "done"})

    audit = CompactionRuntimeState(metadata).memory_audit()

    assert metadata["next_step"] == "llm_call"
    assert metadata["step_count"] == 2
    assert metadata["terminal_output"] == {"answer": "done"}
    assert audit["token_pressure"] == {"state": "ok"}
    assert audit["retained_digest_ids"] == ["dig_1"]
