"""Core runtime runner tests (resume, limits, tool stage integration)."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    fake_noop_tool_executor,
)
from agent_driver.runtime.errors import RuntimeExecutionError


class _SlowProvider(FakeProvider):
    """Provider that blocks longer than the run deadline."""

    async def complete(self, request):  # noqa: ANN001
        await asyncio.sleep(10)
        return await super().complete(request)


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
    with pytest.raises(RuntimeExecutionError):
        await resume_runner.run(
            AgentRunInput(
                resume=ResumeCommand(
                    interrupt_id=latest.ref.checkpoint_id, action=ResumeAction.APPROVE
                ),
                agent_id="agent-test",
                graph_preset="single_react",
            )
        )


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
async def test_single_agent_runner_deadline_interrupts_blocking_step() -> None:
    """Run deadline should cancel an in-flight provider call, not wait forever."""
    provider = _SlowProvider(response_text="too late")
    events = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=events,
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_deadline_blocking_step",
            agent_id="agent-test",
            graph_preset="single_react",
            deadline_seconds=0.01,
        )
    )
    assert output.status.value == "timed_out"
    assert output.terminal_reason.value == "deadline_exceeded"
    assert any(
        event.type.value == "run_failed"
        and event.payload.get("reason") == "deadline_exceeded"
        for event in events.list_for_run("run_deadline_blocking_step")
    )


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
