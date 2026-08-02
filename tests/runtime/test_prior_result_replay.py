"""F2 / U3 (epic 051) — duplicate approve replays the prior terminal output."""

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
    InMemoryApprovalConsumptionStore,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.runtime.errors import ResumeConflictError
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


def _runner(store, *, replay: bool):
    registry = ToolRegistry()
    calls: list[dict] = []

    async def _danger(args):
        calls.append(dict(args))
        return {"summary": f"danger:{args['target']}"}

    registry.register(danger_tool_manifest(), _danger)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry)),
            approval_store=store,
            replay_prior_result=replay,
        ),
    )
    return runner, calls


async def _pause(runner, run_id):
    paused = await runner.run(
        AgentRunInput(
            input="hi",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=planned_danger_tool_policy(),
        )
    )
    assert paused.status.value == "paused"
    return paused


def _approve(run_id, interrupt_id):
    return AgentRunInput(
        run_id=run_id,
        resume=ResumeCommand(interrupt_id=interrupt_id, action=ResumeAction.APPROVE),
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


@pytest.mark.asyncio
async def test_duplicate_approve_replays_prior_output() -> None:
    store = InMemoryApprovalConsumptionStore()
    runner, calls = _runner(store, replay=True)
    paused = await _pause(runner, "run_replay")
    first = await runner.run(_approve("run_replay", paused.interrupt.interrupt_id))
    assert first.status.value == "completed"
    assert len(calls) == 1
    # Duplicate approve → the prior output is replayed verbatim, tool not re-run.
    second = await runner.run(_approve("run_replay", paused.interrupt.interrupt_id))
    assert second.status.value == "completed"
    assert second.run_id == first.run_id
    assert second.answer == first.answer
    assert len(calls) == 1  # exactly one tool side effect


@pytest.mark.asyncio
async def test_without_replay_flag_duplicate_still_conflicts() -> None:
    store = InMemoryApprovalConsumptionStore()
    runner, calls = _runner(store, replay=False)
    paused = await _pause(runner, "run_noreplay")
    await runner.run(_approve("run_noreplay", paused.interrupt.interrupt_id))
    assert len(calls) == 1
    # Backward-compatible: without the flag a duplicate is a stable conflict.
    with pytest.raises(ResumeConflictError):
        await runner.run(_approve("run_noreplay", paused.interrupt.interrupt_id))
    assert len(calls) == 1
