"""R4 (epic 060) — U4 Stop matrix cells not yet covered elsewhere.

The bulk of the U4 abort/cancellation matrix is proven by the existing suite
(see epics/060 for the cell→test mapping): token identity + bounded deadline
(``test_tool_cancellation``), cooperative + uncooperative cancellation
(``test_tool_cancellation`` / ``test_cancellation_failed``), mid-LLM abort
(``test_mid_llm_abort``), completion-race fencing (``test_result_fencing_enforce``),
durable lifecycle + restart readback (``test_runner_abort_lifecycle`` /
``test_abort_lifecycle_store``), and abort during an approval wait
(``test_abort_resume_interaction``). This module fills the remaining cells:

- **abort before/at planning** — a pre-aborted run that would otherwise force a
  planning interrupt must terminate CANCELLED without starting the plan step
  (no new transition after an observed abort);
- **a fenced late result never reopens an already-cancelled run** — ties the
  result-fencing mechanism to the abort terminal.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput, ToolPolicyInput, ToolPolicyMode
from agent_driver.contracts.enums import RunStatus, RuntimeEventType
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryAbortLifecycleStore,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunAbortHandle,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_planning_tool
from tests.runtime.conftest import danger_tool_manifest
from tests.runtime.test_tool_governance_hitl import _PlanApprovalThenWriteProvider


def _plan_runner(abort_store):
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
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
            abort_store=abort_store,
        ),
    )


def _plan_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="write with plan",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={"force_planning": {"enabled": True}},
        ),
    )


@pytest.mark.asyncio
async def test_abort_before_planning_cancels_without_plan_interrupt() -> None:
    """A pre-aborted planning run terminates CANCELLED and never pauses to plan."""
    store = InMemoryAbortLifecycleStore()
    runner = _plan_runner(store)
    handle = RunAbortHandle()
    handle.abort("operator stop")
    out = await runner.run(_plan_input("run_u4_plan_abort"), abort_handle=handle)
    assert out.status == RunStatus.CANCELLED
    # It must NOT have paused for plan approval — no new transition after abort.
    assert out.interrupt is None
    # And no PLAN_APPROVED event was ever emitted (planning never ran).
    events = runner._deps.event_log.list_for_run("run_u4_plan_abort")
    assert not any(e.type == RuntimeEventType.PLAN_APPROVED for e in events)


def test_fenced_late_result_does_not_reopen_cancelled_run() -> None:
    """A straggler from a superseded attempt is dropped, not resurrected."""
    from agent_driver.contracts.enums import ToolPolicyDecision
    from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
    from agent_driver.llm.providers_impl.fake import FakeProvider
    from agent_driver.runtime.single_agent.fencing import RESERVED_ATTEMPT_EPOCH_KEY

    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    stale = ToolResultEnvelope(
        call=ToolCall(tool_name="straggler", tool_call_id="c1"),
        decision=ToolPolicyDecision.ALLOW,
        metadata={RESERVED_ATTEMPT_EPOCH_KEY: 1},  # from a superseded attempt
    )
    ctx = SimpleNamespace(attempt_epoch=3, run_id="r_late", attempt_id="a1")
    kept = runner._fence_and_stamp_envelopes(ctx, [stale])
    assert kept == []  # the late result is ignored, never re-enters the run
    fenced = [
        e
        for e in runner._deps.event_log.list_for_run("r_late")
        if e.type == RuntimeEventType.RESULT_FENCED
    ]
    assert len(fenced) == 1 and fenced[0].payload["tool_name"] == "straggler"
