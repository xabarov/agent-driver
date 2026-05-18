"""Append denied/blocked tool outcomes to a governed execution result."""

from __future__ import annotations

from agent_driver.contracts.enums import GuardrailDecision, ToolPolicyDecision
from agent_driver.contracts.tools import ToolError, ToolResultEnvelope
from agent_driver.tools.executor.result import GovernedExecutionResult
from agent_driver.tools.executor.specs import BlockSpec
from agent_driver.tools.executor.trace import build_denied_trace_for_block


def append_blocked_call(
    *,
    result: GovernedExecutionResult,
    spec: BlockSpec,
) -> None:
    """Append a denied envelope/trace pair for policy or guardrail blocks."""
    metadata = {"policy_reason": spec.reason}
    if spec.stage is not None:
        metadata = {"guardrail_stage": spec.stage}
    result.append(
        envelope=ToolResultEnvelope(
            call=spec.call,
            decision=ToolPolicyDecision.DENY,
            guardrail_decision=(
                GuardrailDecision.BLOCK
                if spec.stage is not None
                else GuardrailDecision.ALLOW
            ),
            error=ToolError(code=spec.code, message=spec.reason),
            metadata=metadata,
        ),
        trace=build_denied_trace_for_block(spec),
    )
