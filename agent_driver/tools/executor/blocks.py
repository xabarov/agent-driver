"""Append denied/blocked tool outcomes to a governed execution result."""

from __future__ import annotations

from collections.abc import Sequence

from agent_driver.contracts.enums import GuardrailDecision, ToolPolicyDecision
from agent_driver.contracts.tools import ToolError, ToolResultEnvelope
from agent_driver.tools.executor.result import GovernedExecutionResult
from agent_driver.tools.executor.specs import BlockSpec
from agent_driver.tools.executor.trace import build_denied_trace_for_block


def disallowed_management_tool_remediation(
    *, tool_name: str, allowed_tools: Sequence[str]
) -> dict[str, object]:
    """Structured repair payload for a management tool denied by the allowlist.

    A scoped workflow node restricts ``allowed_tools`` to real executable tools;
    a model may still emit an out-of-schema management call (``todo_write`` …).
    Rather than a bare ``policy_denied`` observation that the model reads as "I
    have no tools", this returns a typed, machine-readable hint telling it the
    management tool is unavailable *for this run* and which executable tools to
    use instead — so the next turn retries productively.
    """
    allowed = [str(name) for name in allowed_tools if str(name)]
    allowed_text = ", ".join(allowed) if allowed else "(none configured)"
    return {
        "error_kind": "disallowed_management_tool",
        "blocked_tool": tool_name,
        "allowed_tools": allowed,
        "retry_expected": True,
        "remediation": (
            f"The management tool '{tool_name}' is unavailable for this run "
            "(this node executes a fixed tool allowlist). Do not call it again. "
            f"Call one of the allowed executable tools now: {allowed_text}."
        ),
    }


def _force_planning_remediation(spec: BlockSpec) -> dict[str, object] | None:
    if spec.code != "policy_denied":
        return None
    if "force planning requires an approved plan" not in spec.reason:
        return None
    return {
        "error_kind": "force_planning_required",
        "remediation": (
            "Before retrying this side-effecting tool, enter plan mode, create "
            "a concrete plan, and call exit_plan_mode_v2 so the user can approve it"
        ),
        "next_tools": ["enter_plan_mode", "exit_plan_mode_v2"],
        "blocked_tool": spec.call.tool_name,
    }


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
            structured_output=spec.structured_output
            or _force_planning_remediation(spec),
            metadata=metadata,
        ),
        trace=build_denied_trace_for_block(spec),
    )
