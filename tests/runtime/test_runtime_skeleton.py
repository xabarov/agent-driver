"""Tests for phase-2 runtime skeleton components."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import ResumeAction, RuntimeEventType
from agent_driver.contracts.events import new_runtime_event
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    SqliteRuntimeStore,
    fake_noop_tool_executor,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.state import RuntimeState
from tests.runtime.store_assertions import assert_checkpoint_save_load_round_trip


def test_inmemory_checkpoint_store_save_and_latest() -> None:
    """Checkpoint store should persist and return latest row per run."""
    store = InMemoryCheckpointStore()
    input_payload = AgentRunInput(
        input="hello",
        agent_id="agent",
        graph_preset="single_react",
        run_id="run_1",
    )
    state = RuntimeState(run_input=input_payload)
    first_ref = store.save(graph_id="g1", node_id="n1", state=state)
    second_ref = store.save(graph_id="g1", node_id="n2", state=state)
    latest = store.latest("run_1")
    assert latest is not None
    assert latest.ref.checkpoint_id == second_ref.checkpoint_id
    assert latest.ref.parent_checkpoint_id == first_ref.checkpoint_id
    loaded = store.load(first_ref.checkpoint_id)
    assert loaded is not None
    assert loaded.ref.checkpoint_id == first_ref.checkpoint_id
    listed = store.list_checkpoints("run_1")
    assert [row.ref.checkpoint_id for row in listed] == [
        second_ref.checkpoint_id,
        first_ref.checkpoint_id,
    ]
    debug_snapshot = store.snapshot_debug()
    assert "run_1" in debug_snapshot
    caps = store.capabilities()
    assert caps.supports_snapshot_debug


def test_inmemory_event_log_after_seq_filter() -> None:
    """Event log should support filtering by sequence number."""
    events = InMemoryEventLog()
    run_id = "run_evt_1"
    for seq in (1, 2, 3):
        events.append(
            new_runtime_event(
                event_type=RuntimeEventType.NODE_COMPLETED,
                context={"run_id": run_id, "attempt_id": "att_1", "seq": seq},
            )
        )
    assert len(events.list_for_run(run_id)) == 3
    filtered = events.list_for_run(run_id, after_seq=1)
    assert [event.seq for event in filtered] == [2, 3]
    caps = events.capabilities()
    assert not caps.transactional_writes


@pytest.mark.asyncio
async def test_fake_single_step_runner_persists_events_and_checkpoint() -> None:
    """Runner should produce output, events, and checkpoint in one step."""
    provider = FakeProvider(response_text="runner answer")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    run_input = AgentRunInput(
        input="hello runner",
        messages=[ChatMessage(role="user", content="hello runner")],
        run_id="run_test_runtime",
        agent_id="agent-test",
        graph_preset="single_react",
    )
    output = await runner.run(run_input)

    assert output.answer == "runner answer"
    assert output.checkpoint is not None
    assert output.status.value == "completed"
    run_events = events.list_for_run("run_test_runtime")
    assert len(run_events) >= 2
    assert any(event.type.value == "run_completed" for event in run_events)


@pytest.mark.asyncio
async def test_single_agent_runner_resume_after_injected_failure() -> None:
    """Runner should resume from checkpoint after injected step failure."""
    provider = FakeProvider(response_text="resume answer")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    failing = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        config=RunnerConfig(fail_after_step="llm_call"),
    )
    run_input = AgentRunInput(
        input="hello runner",
        run_id="run_resume_1",
        agent_id="agent-test",
        graph_preset="single_react",
    )
    with pytest.raises(RuntimeExecutionError):
        await failing.run(run_input)

    latest = checkpoints.latest("run_resume_1")
    assert latest is not None
    resume_runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    resumed_output = await resume_runner.run(
        AgentRunInput(
            resume=ResumeCommand(
                interrupt_id=latest.ref.checkpoint_id, action=ResumeAction.APPROVE
            ),
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert resumed_output.status.value == "completed"
    assert resumed_output.answer == "resume answer"
    run_events = events.list_for_run("run_resume_1")
    assert any(event.type.value == "run_resumed" for event in run_events)


@pytest.mark.asyncio
async def test_single_agent_runner_cancellation() -> None:
    """Runner should emit cancelled terminal state when probe is set."""
    provider = FakeProvider(response_text="ignored")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        config=RunnerConfig(cancellation_probe=lambda: True),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_cancel_1",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.status.value == "cancelled"
    assert output.terminal_reason.value == "cancelled_by_user"


@pytest.mark.asyncio
async def test_single_agent_runner_deadline_timeout() -> None:
    """Runner should return timed_out when deadline is exceeded."""
    provider = FakeProvider(response_text="slow")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_deadline_1",
            agent_id="agent-test",
            graph_preset="single_react",
            deadline_seconds=0.000001,
        )
    )
    assert output.status.value == "timed_out"
    assert output.terminal_reason.value == "deadline_exceeded"


@pytest.mark.asyncio
async def test_single_agent_runner_max_steps_exceeded() -> None:
    """Runner should fail when max_steps budget is reached."""
    provider = FakeProvider(response_text="hello")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_steps_1",
            agent_id="agent-test",
            graph_preset="single_react",
            max_steps=1,
        )
    )
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "max_steps_exceeded"


@pytest.mark.asyncio
async def test_fake_tool_executor_is_used_by_runner() -> None:
    """Runner should invoke custom tool executor in tool stage."""
    calls = {"count": 0}

    async def _counting_executor(run_input: AgentRunInput, llm_response: LlmResponse):
        calls["count"] += 1
        return await fake_noop_tool_executor(run_input, llm_response)

    provider = FakeProvider(response_text="tools")
    checkpoints = InMemoryCheckpointStore()
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=checkpoints,
        event_log=events,
        config=RunnerConfig(tool_executor=_counting_executor),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_tools_1",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    assert output.status.value == "completed"
    assert calls["count"] == 1


def test_sqlite_runtime_store_round_trip(tmp_path) -> None:
    """SQLite runtime store should persist checkpoints and events."""
    store = SqliteRuntimeStore(path=str(tmp_path / "runtime.db"))
    run_input = AgentRunInput(
        input="hello",
        run_id="run_sqlite_1",
        agent_id="agent-test",
        graph_preset="single_react",
    )
    state = RuntimeState(run_input=run_input, metadata={"next_step": "llm_call"})
    assert_checkpoint_save_load_round_trip(
        store=store,
        graph_id="single_agent_runtime",
        node_id="run_started",
        state=state,
    )
    listed = store.list_checkpoints("run_sqlite_1", limit=1)
    assert len(listed) == 1
    assert listed[0].ref.run_id == "run_sqlite_1"
    debug_snapshot = store.snapshot_debug()
    assert "run_sqlite_1" in debug_snapshot
    caps = store.capabilities()
    assert caps.transactional_writes

    event = new_runtime_event(
        event_type=RuntimeEventType.RUN_STARTED,
        context={"run_id": "run_sqlite_1", "attempt_id": "attempt_1", "seq": 1},
    )
    store.append(event)
    events = store.list_for_run("run_sqlite_1")
    assert len(events) == 1
    assert events[0].event_id == event.event_id
