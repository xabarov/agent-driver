"""Regression: a host-provided approval heading (ToolGateAsk.title, carried on
ToolPolicyOutcome.interrupt_title) overrides the default English interrupt title.

Without this, a host with a localised UI (e.g. excel-ai's Russian
"Подтвердить правку: …") got the hardcoded "Approval required for '<tool>'"
heading — the title was documented as overridable but silently dropped.
"""

from __future__ import annotations

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall
from agent_driver.contracts.tools.policy import ToolPolicyDecision, ToolPolicyOutcome
from agent_driver.tools.executor.policy_interrupt import build_tool_approval_interrupt
from agent_driver.tools.executor.specs import ToolApprovalContext, safe_manifest


def _ctx(*, interrupt_title: str | None) -> ToolApprovalContext:
    return ToolApprovalContext(
        run_input=AgentRunInput(
            input="x", run_id="r1", agent_id="a", graph_preset="single_react"
        ),
        call=ToolCall(tool_name="excel_set_cell", args={"cell_ref": "Z1"}, tool_call_id="c1"),
        index=0,
        manifest=safe_manifest("excel_set_cell"),
        policy=ToolPolicyOutcome(
            decision=ToolPolicyDecision.INTERRUPT,
            reason="Агент собирается выполнить правку.",
            interrupt_reason="approval_required",
            interrupt_title=interrupt_title,
        ),
        run_metadata={},
    )


def test_interrupt_uses_host_title_when_provided() -> None:
    interrupt = build_tool_approval_interrupt(
        _ctx(interrupt_title="Подтвердить правку: excel_set_cell?")
    )
    assert interrupt.title == "Подтвердить правку: excel_set_cell?"
    # Host message still flows to the description.
    assert interrupt.description == "Агент собирается выполнить правку."


def test_interrupt_falls_back_to_default_title_when_absent() -> None:
    interrupt = build_tool_approval_interrupt(_ctx(interrupt_title=None))
    assert interrupt.title == "Approval required for 'excel_set_cell'"
