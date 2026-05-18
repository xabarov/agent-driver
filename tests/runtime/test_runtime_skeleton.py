"""Tests for phase-2 runtime skeleton components."""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
)
from agent_driver.runtime.state import RuntimeState


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
    assert len(run_events) == 2
    assert run_events[-1].type.value == "run_completed"
