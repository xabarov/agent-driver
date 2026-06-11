"""Tests for compatibility-preserving runtime metadata views."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_driver.runtime.metadata_state import (
    CompactionRuntimeState,
    LoopControlState,
    PlanningRuntimeState,
    ResearchRuntimeState,
    StreamingRuntimeState,
    ToolLoopState,
    get_compaction_runtime_state,
    get_loop_control_state,
    get_tool_loop_state,
)


@dataclass
class _MetadataContext:
    metadata: dict[str, object] = field(default_factory=dict)


def test_planning_state_preserves_existing_metadata_keys() -> None:
    metadata = {"planning_state_seed": {"todos": []}}
    state = PlanningRuntimeState(metadata)

    assert state.pop_seed() == {"todos": []}
    state.set_planning_state({"todos": []})
    state.set_planning_step({"step_id": "plan_1"})
    state.set_last_todo_write_signature("sig")
    state.mark_todo_deduped()
    state.increment_tool_loops_since_todo_write()
    state.increment_todo_hint_count("todo_1")
    state.reset_todo_write_loop_counters(in_progress_id="todo_2")

    assert metadata == {
        "planning_state": {"todos": []},
        "planning_step": {"step_id": "plan_1"},
        "last_todo_write_signature": "sig",
        "todo_write_deduped": True,
        "tool_loops_since_todo_write": 0,
        "last_in_progress_id": "todo_2",
    }


def test_streaming_state_preserves_streaming_lifecycle_shape() -> None:
    metadata: dict[str, object] = {}
    state = StreamingRuntimeState(metadata)

    state.mark_started()
    assert state.started() is True
    assert state.completed() is False
    state.set_content("hello")
    assert state.content() == "hello"
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
    ToolLoopState(metadata).append_stage_outputs(
        traces=[{"tool_name": "python"}],
        results=[{"call": {"tool_name": "python"}}],
    )
    assert ToolLoopState(metadata).tool_trace()[-1] == {"tool_name": "python"}
    assert ToolLoopState(metadata).tool_results()[-1] == {
        "call": {"tool_name": "python"}
    }
    ToolLoopState(metadata).force_final_answer(reason="done")
    ToolLoopState(metadata).ensure_force_final_answer(reason="ignored")
    ResearchRuntimeState(metadata).set_contract(
        payload={"final_readiness": {"status": "allowed"}},
        status="allowed",
        reasons=[],
    )

    assert metadata["force_final_answer"] is True
    assert metadata["tool_choice_override"] == "none"
    assert metadata["force_final_answer_reason"] == "done"
    assert ToolLoopState(metadata).force_final_answer_enabled() is True
    ToolLoopState(metadata).clear_force_final_answer()
    assert "force_final_answer" not in metadata
    assert "tool_choice_override" not in metadata
    assert "force_final_answer_reason" not in metadata
    assert metadata["research_session_contract"] == {
        "final_readiness": {"status": "allowed"}
    }
    assert metadata["final_readiness"] == "allowed"
    assert metadata["repair_required_reasons"] == []


def test_loop_and_compaction_state_preserve_output_shapes() -> None:
    metadata: dict[str, object] = {
        "token_pressure": {"state": "ok"},
        "retained_digest_ids": ["dig_1"],
        "observations": [{"summary": "note"}],
        "trim_audit": [{"kept": 1}],
        "trim_metadata": {"mode": "deterministic"},
        "microcompaction_audit": [{"kind": "micro"}],
        "microcompaction": {"enabled": True},
        "post_compact_cleanup": {"removed": []},
        "session_memory_extraction": {"updated": False},
        "prompt_render": {"messages": 2},
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

    projection = CompactionRuntimeState(metadata).output_metadata_projection()
    assert projection == {
        "observations": [{"summary": "note"}],
        "trim_audit": [{"kept": 1}],
        "trim_metadata": {"mode": "deterministic"},
        "microcompaction_audit": [{"kind": "micro"}],
        "microcompaction": {"enabled": True},
        "token_pressure": {"state": "ok"},
        "compaction_decision": None,
        "compaction_audit": None,
        "compaction_result": None,
        "compaction_failures": [],
        "post_compact_cleanup": {"removed": []},
        "session_memory_extraction": {"updated": False},
        "prompt_render": {"messages": 2},
    }
    CompactionRuntimeState(metadata).set_microcompaction(
        observations=[{"summary": "new"}],
        audit=[{"kind": "new"}],
        bytes_saved=7,
        estimated_tokens_saved=2,
    )
    assert metadata["observations"] == [{"summary": "new"}]
    assert metadata["microcompaction"] == {
        "bytes_saved": 7,
        "estimated_tokens_saved": 2,
    }


def test_get_state_helpers_wrap_context_metadata() -> None:
    context = _MetadataContext(
        {
            "tool_calls": 3,
            "token_pressure": {"state": "early_warning"},
            "workspace_cwd": "/tmp/work",
        }
    )

    loop = get_loop_control_state(context)
    tools = get_tool_loop_state(context)
    compaction = get_compaction_runtime_state(context)

    loop.llm_step_count = 4
    tools.tool_calls = 5

    assert loop.workspace_cwd() == "/tmp/work"
    assert context.metadata["llm_step_count"] == 4
    assert context.metadata["tool_calls"] == 5
    assert compaction.token_pressure() == {"state": "early_warning"}
