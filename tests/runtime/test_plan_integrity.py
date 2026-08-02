"""U5 (epic 053) — plan-integrity: authoritative hash, EDIT re-hash, host binding.

Covers the slice:
- detect_plan_revision() detects a material change vs the approved hash;
- an approved plan's recorded content_hash is HARNESS-authored (equals
  plan_content_hash of the actual content), so it can't be forged;
- editing the plan on resume re-hashes from the EDITED content (no stale hash);
- an opaque host policy-binding on the resume survives into the approved plan.

Reuses the plan-approval provider from test_tool_governance_hitl.
"""

from __future__ import annotations

import pytest

from agent_driver.context.planning import detect_plan_revision, plan_content_hash
from agent_driver.contracts import (
    AgentRunInput,
    ResumeAction,
    ToolPolicyInput,
    ToolPolicyMode,
)
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


def test_detect_plan_revision() -> None:
    h = plan_content_hash(_PLAN_CONTENT)
    assert detect_plan_revision(h, _PLAN_CONTENT) is False
    assert detect_plan_revision(h, _PLAN_CONTENT + " and delete") is True
    # Empty approved hash → fail-safe: treat as a revision (require approval).
    assert detect_plan_revision("", "anything") is True


def _runner():
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
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry))
        ),
    )


def _policy():
    return ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        metadata={"force_planning": {"enabled": True}},
    )


async def _pause_on_plan(runner, run_id: str):
    paused = await runner.run(
        AgentRunInput(
            input="write with plan",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None
    return paused


@pytest.mark.asyncio
async def test_approved_plan_hash_is_harness_authoritative() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_plan_hash")
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_plan_hash",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    approved = resumed.metadata["approved_plan"]
    # The recorded hash is the real SHA-256 of the plan content — not trusted
    # from any model-supplied content_hash field.
    assert approved["content_hash"] == plan_content_hash(_PLAN_CONTENT)
    assert detect_plan_revision(approved["content_hash"], _PLAN_CONTENT) is False


@pytest.mark.asyncio
async def test_edit_rehashes_from_edited_content() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_plan_edit")
    edited_content = "1. Inspect only\n2. Stop"
    edited = await runner.run(
        AgentRunInput(
            run_id="run_plan_edit",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.EDIT,
                edited_tool_args={"content": edited_content},
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    approved = edited.metadata["approved_plan"]
    # The recorded hash reflects the EDITED plan, not the original (no stale hash).
    assert approved["content_hash"] == plan_content_hash(edited_content)
    assert approved["content_hash"] != plan_content_hash(_PLAN_CONTENT)


@pytest.mark.asyncio
async def test_host_policy_binding_survives_into_approved_plan() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_plan_binding")
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_plan_binding",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
                approved_by="operator-7",
                metadata={"plan_policy_binding": "policy-snapshot-42"},
            ),
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=_policy(),
        )
    )
    approved = resumed.metadata["approved_plan"]
    assert approved["policy_binding"] == "policy-snapshot-42"
    assert approved["approved_by"] == "operator-7"
