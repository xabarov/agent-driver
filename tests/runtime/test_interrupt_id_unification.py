"""U2 (epic 050) — unified interrupt-id scheme across both interrupt builders."""

from __future__ import annotations

from typing import Any

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.runtime.tool_gate import ToolGateAsk, ToolGateContext
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_planning_tool
from agent_driver.tools.executor.interrupt_ids import build_attempt_id, build_interrupt_id
from tests.runtime.conftest import (
    danger_tool_manifest,
    llm_request_with_planned_calls,
)
from tests.runtime.test_tool_governance_hitl import _PlanApprovalThenWriteProvider


def test_build_helpers() -> None:
    assert build_interrupt_id(run_id="r", tool_call_id="tc1", index=2) == "int_r_tc1"
    # No tool_call_id → equals the historical tool-approval scheme (index-based).
    assert build_interrupt_id(run_id="r", tool_call_id=None, index=2) == "int_r_2"
    assert build_interrupt_id(run_id=None, tool_call_id="", index=0) == "int_runtime_0"
    assert build_attempt_id(index=3) == "attempt_3"
    assert build_attempt_id(index=3, attempt_id="a9") == "a9"


@pytest.mark.asyncio
async def test_gate_ask_interrupt_id_is_run_scoped_and_call_stable() -> None:
    """policy_interrupt path (tool-gate ASK)."""
    registry = ToolRegistry()

    async def _h(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    registry.register(
        ToolManifest(
            name="lookup",
            description="read",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _h,
    )
    executor = GovernedToolExecutor(registry=registry)

    async def gate(ctx: ToolGateContext) -> ToolGateAsk:
        return ToolGateAsk(message="approve?")

    run_input = AgentRunInput(
        input="hi",
        run_id="run_gate",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    call = ToolCall(tool_name="lookup", tool_call_id="tc-42", args={})
    response = await provider.complete(llm_request_with_planned_calls(planned=[call]))
    result = await executor.execute(run_input, response, tool_gate=gate)
    assert result.interrupt is not None
    assert result.interrupt.interrupt_id == "int_run_gate_tc-42"


@pytest.mark.asyncio
async def test_plan_approval_interrupt_id_uses_same_scheme() -> None:
    """allowed.py path (plan approval) — same run-scoped + call-stable scheme."""
    registry = ToolRegistry()
    register_planning_tool(registry)

    async def _fw(args):
        return {"summary": "wrote"}

    registry.register(
        danger_tool_manifest().model_copy(
            update={"name": "file_write", "risk": "medium", "side_effect": "reversible_write"}
        ),
        _fw,
    )
    runner = FakeSingleStepRunner(
        provider=_PlanApprovalThenWriteProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(GovernedToolExecutor(registry=registry))
        ),
    )
    paused = await runner.run(
        AgentRunInput(
            input="plan",
            run_id="run_plan",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                mode=ToolPolicyMode.ALLOW_TOOLS,
                metadata={"force_planning": {"enabled": True}},
            ),
        )
    )
    assert paused.status.value == "paused"
    # The exit_plan_mode_v2 call has tool_call_id "plan_call"; same scheme.
    assert paused.interrupt.interrupt_id == "int_run_plan_plan_call"
