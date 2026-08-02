"""U5 (epic 053) — plan-approval resume writes a durable PlanArtifact."""

from __future__ import annotations

import pytest

from agent_driver.context.planning import plan_content_hash
from agent_driver.context.planning.artifacts import (
    InMemoryPlanArtifactStore,
    SqlitePlanArtifactStore,
)
from agent_driver.contracts import (
    AgentRunInput,
    ResumeAction,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.contracts.enums import PlanningModeState
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_planning_tool
from tests.runtime.conftest import danger_tool_manifest
from tests.runtime.test_tool_governance_hitl import _PlanApprovalThenWriteProvider

_PLAN_CONTENT = "1. Inspect\n2. Write\n3. Verify"


def _runner(store):
    registry = ToolRegistry()
    register_planning_tool(registry)

    async def _file_write(args):
        return {"summary": "wrote"}

    registry.register(
        danger_tool_manifest().model_copy(
            update={
                "name": "file_write",
                "description": "Write file",
                "risk": "medium",
                "side_effect": "reversible_write",
            }
        ),
        _file_write,
    )
    return FakeSingleStepRunner(
        provider=_PlanApprovalThenWriteProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry)),
            plan_artifact_store=store,
        ),
    )


def _policy():
    return ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        metadata={"force_planning": {"enabled": True}},
    )


async def _pause(runner, run_id):
    paused = await runner.run(
        AgentRunInput(
            input="plan then write",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    assert paused.status.value == "paused"
    return paused


@pytest.mark.asyncio
async def test_approved_plan_persists_artifact(tmp_path) -> None:
    store = SqlitePlanArtifactStore(path=str(tmp_path / "plans.db"))
    runner = _runner(store)
    paused = await _pause(runner, "run_pa_ok")
    await runner.run(
        AgentRunInput(
            run_id="run_pa_ok",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
                approved_by="operator-1",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    art = store.get("plan_force_1")
    assert art is not None
    assert art.status is PlanningModeState.APPROVED
    assert art.content_hash == plan_content_hash(_PLAN_CONTENT)
    assert art.approved_by == "operator-1"
    # Durable across a restart.
    reopened = SqlitePlanArtifactStore(path=str(tmp_path / "plans.db"))
    assert reopened.get("plan_force_1").status is PlanningModeState.APPROVED


@pytest.mark.asyncio
async def test_rejected_plan_persists_rejected_artifact() -> None:
    store = InMemoryPlanArtifactStore()
    runner = _runner(store)
    paused = await _pause(runner, "run_pa_reject")
    await runner.run(
        AgentRunInput(
            run_id="run_pa_reject",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
                approved_by="operator-2",
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    art = store.get("plan_force_1")
    assert art is not None and art.status is PlanningModeState.REJECTED


@pytest.mark.asyncio
async def test_no_store_is_backward_compatible() -> None:
    runner = _runner(None)
    paused = await _pause(runner, "run_pa_none")
    out = await runner.run(
        AgentRunInput(
            run_id="run_pa_none",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id, action=ResumeAction.APPROVE
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    assert out.status.value == "completed"
