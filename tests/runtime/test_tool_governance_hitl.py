"""HITL pause/resume integration tests for governed runtime."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ResumeAction,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.runtime.errors import MissingCheckpointError, RuntimeExecutionError
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


@pytest.mark.asyncio
async def test_runner_interrupts_for_high_risk_policy() -> None:
    """Runner should return paused output when policy requests interrupt."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    governed = GovernedToolExecutor(registry=registry)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=wrap_governed_executor(governed)),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_interrupt_1",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert any(event.type.value == "interrupt_requested" for event in output.events)


@pytest.mark.asyncio
async def test_runner_resume_approve_executes_pending_tool_once() -> None:
    """Approve resume should execute pending call exactly once."""
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args['target']}"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_approve",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None
    resume_output = await runner.run(
        AgentRunInput(
            run_id="run_hitl_approve",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resume_output.status.value == "completed"
    assert len(calls) == 1
    assert calls[0]["target"] == "x"


@pytest.mark.asyncio
async def test_runner_resume_edit_applies_edited_args() -> None:
    """Edit resume should execute approved call with edited args."""
    registry = ToolRegistry()
    calls: list[dict[str, object]] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args['target']}"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_edit",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_hitl_edit",
            resume=ResumeCommand(
                interrupt_id=(
                    paused.interrupt.interrupt_id if paused.interrupt else "missing"
                ),
                action=ResumeAction.EDIT,
                edited_tool_args={"target": "edited"},
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )
    assert resumed.status.value == "completed"
    assert calls == [{"target": "edited"}]


@pytest.mark.asyncio
async def test_runner_resume_reject_and_cancel_are_terminal() -> None:
    """Reject/cancel resume actions should terminate deterministically."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    checkpoint_store = InMemoryCheckpointStore()
    event_log = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=checkpoint_store,
        event_log=event_log,
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_terminal",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.interrupt is not None
    rejected = await runner.run(
        AgentRunInput(
            run_id="run_hitl_terminal",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
            ),
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert rejected.status.value == "failed"
    assert rejected.terminal_reason.value == "approval_rejected"

    paused_again = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_cancel",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    cancelled = await runner.run(
        AgentRunInput(
            run_id="run_hitl_cancel",
            resume=ResumeCommand(
                interrupt_id=(
                    paused_again.interrupt.interrupt_id
                    if paused_again.interrupt
                    else "missing"
                ),
                action=ResumeAction.CANCEL,
            ),
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert cancelled.status.value == "cancelled"
    assert cancelled.terminal_reason.value == "cancelled_by_user"


@pytest.mark.asyncio
async def test_runner_resume_clarify_continues_with_clarification() -> None:
    """Clarify resume should continue run and include clarification in metadata."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_clarify",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_hitl_clarify",
            resume=ResumeCommand(
                interrupt_id=(
                    paused.interrupt.interrupt_id if paused.interrupt else "missing"
                ),
                action=ResumeAction.CLARIFY,
                message="Use safer approach",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.NO_TOOLS),
        )
    )
    assert resumed.status.value == "completed"
    assert any(event.type.value == "run_resumed" for event in resumed.events)


@pytest.mark.asyncio
async def test_runner_resume_rejects_mismatched_interrupt_id() -> None:
    """Runtime should fail when resume interrupt_id mismatches pending payload."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_hitl_bad_id",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    with pytest.raises(
        MissingCheckpointError, match="Checkpoint 'interrupt_other' not found"
    ):
        await runner.run(
            AgentRunInput(
                run_id="run_hitl_bad_id",
                resume=ResumeCommand(
                    interrupt_id="interrupt_other",
                    action=ResumeAction.APPROVE,
                ),
                agent_id="agent",
                graph_preset="single_react",
            )
        )


@pytest.mark.asyncio
async def test_runner_resume_requires_pending_interrupt() -> None:
    """Runtime should reject resume when checkpoint has no pending interrupt."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    completed = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_no_pending",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert completed.status.value == "completed"
    with pytest.raises(RuntimeExecutionError, match="requires pending interrupt"):
        await runner.run(
            AgentRunInput(
                run_id="run_no_pending",
                resume=ResumeCommand(
                    interrupt_id=(
                        completed.checkpoint.checkpoint_id
                        if completed.checkpoint
                        else "missing"
                    ),
                    action=ResumeAction.APPROVE,
                ),
                agent_id="agent",
                graph_preset="single_react",
            )
        )
