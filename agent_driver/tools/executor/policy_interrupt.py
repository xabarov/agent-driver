"""Construct structured interrupt requests for approval-gated tool calls."""

from __future__ import annotations

import json

from agent_driver.contracts.enums import InterruptReason, ResumeAction
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.tools.executor.result import GovernedExecutionResult
from agent_driver.tools.executor.specs import ToolApprovalContext
from agent_driver.tools.executor.trace import build_tool_trace, trace_spec_denied


def build_tool_approval_interrupt(ctx: ToolApprovalContext) -> InterruptRequest:
    """Build interrupt payload for human approval of a gated tool call."""
    args_preview = json.dumps(ctx.call.args, ensure_ascii=True, sort_keys=True)
    if len(args_preview) > 280:
        args_preview = args_preview[:280].rstrip() + "..."
    return InterruptRequest(
        interrupt_id=f"int_{ctx.run_input.run_id or 'runtime'}_{ctx.index}",
        run_id=ctx.run_input.run_id or "run_pending",
        attempt_id=f"attempt_{ctx.index}",
        checkpoint_id="checkpoint_pending",
        reason=InterruptReason(ctx.policy.interrupt_reason or "approval_required"),
        title=f"Approval required for '{ctx.call.tool_name}'",
        description=ctx.policy.reason,
        risk=ctx.manifest.risk,
        proposed_action={
            "tool_name": ctx.call.tool_name,
            "tool_call_id": ctx.call.tool_call_id,
            "args": ctx.call.args,
            "args_preview": args_preview,
            "risk": ctx.manifest.risk.value,
            "side_effect": ctx.manifest.side_effect.value,
            "approval_mode": ctx.manifest.approval_mode.value,
        },
        allowed_actions=[
            ResumeAction.APPROVE,
            ResumeAction.REJECT,
            ResumeAction.EDIT,
            ResumeAction.CANCEL,
        ],
        editable_fields=["args"],
        metadata={
            "policy_reason": ctx.policy.reason,
            **ctx.run_metadata,
        },
    )


def record_interrupt_and_trace(
    result: GovernedExecutionResult,
    ctx: ToolApprovalContext,
) -> None:
    """Append interrupt envelope + trace; sets result.interrupt via append()."""
    interrupt = build_tool_approval_interrupt(ctx)
    result.append(
        envelope=ToolResultEnvelope(
            call=ctx.call,
            decision=ctx.policy.decision,
            interrupt=interrupt.model_dump(mode="json"),
            metadata={
                "policy_reason": ctx.policy.reason,
                **ctx.run_metadata,
            },
        ),
        trace=build_tool_trace(
            trace_spec_denied(
                index=ctx.index,
                call=ctx.call,
                manifest=ctx.manifest,
                summary=ctx.policy.reason,
                error_code="approval_required",
            )
        ),
        interrupt=interrupt,
    )
