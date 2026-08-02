"""Real-Postgres resume-path integration (R2 / epic 058 — integration wiring).

The store-unit matrix in ``test_postgres_control_plane.py`` proves the CAS
primitives; this proves the same Postgres approval store wired into the actual
runner **resume path** makes a duplicate/concurrent approval refuse BEFORE the
tool runs a second time, and that a stale ``expected_checkpoint_id`` conflicts
before anything is consumed — the end-to-end "two API workers approve one
interrupt through Postgres" scenario the product depends on.

Whole module carries the ``postgres`` marker → default sweep excludes it; the
mandatory postgres CI job runs it against a real cluster.
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
    wrap_governed_executor,
)
from agent_driver.runtime.control import PostgresApprovalConsumptionStore
from agent_driver.runtime.errors import ResumeConflictError
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import danger_tool_manifest, planned_danger_tool_policy

pytestmark = pytest.mark.postgres


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


def _approve(run_id, interrupt_id, *, expected_checkpoint_id=None):
    return AgentRunInput(
        run_id=run_id,
        resume=ResumeCommand(
            interrupt_id=interrupt_id,
            action=ResumeAction.APPROVE,
            expected_checkpoint_id=expected_checkpoint_id,
        ),
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


@pytest.mark.asyncio
async def test_pg_resume_duplicate_conflicts_end_to_end(pg_control_config) -> None:
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    runner, calls = _runner(store)
    paused = await _pause(runner, "run_pg_dup")
    first = await runner.run(_approve("run_pg_dup", paused.interrupt.interrupt_id))
    assert first.status.value == "completed"
    assert len(calls) == 1
    with pytest.raises(ResumeConflictError, match="already consumed"):
        await runner.run(_approve("run_pg_dup", paused.interrupt.interrupt_id))
    assert len(calls) == 1  # the Postgres CAS refused the second side-effect


@pytest.mark.asyncio
async def test_pg_two_concurrent_resumes_one_side_effect(pg_control_config) -> None:
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    runner, calls = _runner(store)
    paused = await _pause(runner, "run_pg_race")
    approve = _approve("run_pg_race", paused.interrupt.interrupt_id)
    results = await asyncio.gather(
        runner.run(approve), runner.run(approve), return_exceptions=True
    )
    conflicts = [r for r in results if isinstance(r, ResumeConflictError)]
    completed = [
        r
        for r in results
        if not isinstance(r, Exception) and r.status.value == "completed"
    ]
    assert len(conflicts) == 1, results
    assert len(completed) == 1
    assert len(calls) == 1  # exactly one tool side-effect through Postgres


@pytest.mark.asyncio
async def test_pg_stale_checkpoint_conflicts_before_consume(pg_control_config) -> None:
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    runner, calls = _runner(store)
    paused = await _pause(runner, "run_pg_stale")
    with pytest.raises(ResumeConflictError, match="expected checkpoint"):
        await runner.run(
            _approve(
                "run_pg_stale",
                paused.interrupt.interrupt_id,
                expected_checkpoint_id="chk_does_not_match",
            )
        )
    assert calls == []  # stale approval never ran the tool
    # And nothing was consumed in the Postgres ledger — the guard fires first.
    assert store.get(run_id="run_pg_stale", interrupt_id=paused.interrupt.interrupt_id) is None
