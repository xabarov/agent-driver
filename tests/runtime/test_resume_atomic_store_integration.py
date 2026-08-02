"""U3 D (epic 051) — approval store wired into resume: one tool side-effect.

Proves the durable CAS ledger, once configured on the runner, makes a duplicate
or concurrent approval refuse BEFORE the tool runs a second time.
"""

from __future__ import annotations

import asyncio

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
    SqliteApprovalConsumptionStore,
    wrap_governed_executor,
)
from agent_driver.runtime.errors import ResumeConflictError
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy


def _runner(approval_store):
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
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
            approval_store=approval_store,
        ),
    )
    return runner, calls


async def _pause(runner, run_id):
    paused = await runner.run(
        AgentRunInput(
            input="hello",
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
async def test_approval_store_duplicate_resume_conflicts(tmp_path) -> None:
    store = SqliteApprovalConsumptionStore(path=str(tmp_path / "a.db"))
    runner, calls = _runner(store)
    paused = await _pause(runner, "run_store_dup")
    first = await runner.run(_approve("run_store_dup", paused.interrupt.interrupt_id))
    assert first.status.value == "completed"
    assert len(calls) == 1
    with pytest.raises(ResumeConflictError, match="already consumed"):
        await runner.run(_approve("run_store_dup", paused.interrupt.interrupt_id))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_two_concurrent_resumes_run_tool_once(tmp_path) -> None:
    store = SqliteApprovalConsumptionStore(path=str(tmp_path / "b.db"))
    runner, calls = _runner(store)
    paused = await _pause(runner, "run_store_race")
    approve = _approve("run_store_race", paused.interrupt.interrupt_id)
    results = await asyncio.gather(
        runner.run(approve), runner.run(approve), return_exceptions=True
    )
    conflicts = [r for r in results if isinstance(r, ResumeConflictError)]
    completed = [
        r for r in results if not isinstance(r, Exception) and r.status.value == "completed"
    ]
    assert len(conflicts) == 1, results
    assert len(completed) == 1
    assert len(calls) == 1  # exactly one tool side-effect under the race
