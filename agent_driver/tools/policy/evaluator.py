"""Structured policy evaluation for planned tool calls."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import (
    SideEffectClass,
    ToolPolicyDecision,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.tools import (
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyOutcome,
)
from agent_driver.tools.policy.risk import is_risk_at_or_above

_DEFAULT_FORCE_PLANNING_EXEMPT_TOOLS = {
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode_v2",
    "planning_state_update",
    "todo_write",
}
_DEFAULT_FORCE_PLANNING_SIDE_EFFECTS = {
    SideEffectClass.REVERSIBLE_WRITE.value,
    SideEffectClass.IRREVERSIBLE_WRITE.value,
    SideEffectClass.EXTERNAL_ACTION.value,
}


def _force_planning_config(policy: ToolPolicyInput) -> dict[str, Any]:
    raw = policy.metadata.get("force_planning")
    if isinstance(raw, dict):
        return raw
    if policy.metadata.get("force_planning_enabled") is True:
        return {"enabled": True}
    return {}


def _force_planning_has_approved_plan(config: dict[str, Any]) -> bool:
    if config.get("approved") is True:
        return True
    approved_plan_id = config.get("approved_plan_id")
    if isinstance(approved_plan_id, str) and approved_plan_id.strip():
        return True
    approved_plan = config.get("approved_plan")
    if isinstance(approved_plan, dict):
        plan_id = approved_plan.get("plan_id")
        if isinstance(plan_id, str) and plan_id.strip():
            return True
        return approved_plan.get("approved") is True
    return False


def _force_planning_applies(
    *,
    config: dict[str, Any],
    manifest: ToolManifest,
    call: ToolCall,
) -> bool:
    exempt_tools = config.get("exempt_tools")
    if not isinstance(exempt_tools, list):
        exempt = _DEFAULT_FORCE_PLANNING_EXEMPT_TOOLS
    else:
        exempt = {str(item) for item in exempt_tools if str(item).strip()}
    if call.tool_name in exempt:
        return False

    gated_tools = config.get("gated_tools")
    if isinstance(gated_tools, list) and call.tool_name in {
        str(item) for item in gated_tools
    }:
        return True

    gated_side_effects = config.get("gated_side_effects")
    if not isinstance(gated_side_effects, list):
        side_effects = _DEFAULT_FORCE_PLANNING_SIDE_EFFECTS
    else:
        side_effects = {str(item) for item in gated_side_effects if str(item).strip()}
    if manifest.side_effect.value in side_effects:
        return True

    min_risk = config.get("min_risk")
    if isinstance(min_risk, str) and min_risk.strip():
        try:
            threshold = ToolRisk(min_risk.strip())
        except ValueError:
            return False
        return is_risk_at_or_above(manifest, threshold.value)
    return False


def _evaluate_force_planning(
    *,
    policy: ToolPolicyInput,
    manifest: ToolManifest,
    call: ToolCall,
) -> ToolPolicyOutcome | None:
    config = _force_planning_config(policy)
    if config.get("enabled") is not True:
        return None
    if _force_planning_has_approved_plan(config):
        return None
    if not _force_planning_applies(config=config, manifest=manifest, call=call):
        return None
    return ToolPolicyOutcome(
        decision=ToolPolicyDecision.DENY,
        reason=(
            "force planning requires an approved plan before tool "
            f"'{call.tool_name}' can run"
        ),
        metadata={
            "force_planning": {
                "required": True,
                "tool_name": call.tool_name,
                "risk": manifest.risk.value,
                "side_effect": manifest.side_effect.value,
            }
        },
    )


def evaluate_tool_policy(
    *,
    policy: ToolPolicyInput,
    manifest: ToolManifest,
    call: ToolCall,
    current_tool_calls: int,
) -> ToolPolicyOutcome:
    """Evaluate runtime policy for one planned tool call."""
    allowed = set(policy.allowed_tools or [])
    denied = set(policy.denied_tools or [])
    outcome = ToolPolicyOutcome(
        decision=ToolPolicyDecision.ALLOW,
        reason="policy check passed",
    )
    if policy.mode == ToolPolicyMode.NO_TOOLS:
        outcome = ToolPolicyOutcome(
            decision=ToolPolicyDecision.DENY,
            reason="tool usage disabled by run policy mode",
        )
    elif policy.mode == ToolPolicyMode.CLARIFY:
        outcome = ToolPolicyOutcome(
            decision=ToolPolicyDecision.INTERRUPT,
            reason="tool call requires clarification by run policy mode",
            interrupt_reason="clarification_required",
        )
    elif policy.mode == ToolPolicyMode.APPROVAL_REQUIRED:
        outcome = ToolPolicyOutcome(
            decision=ToolPolicyDecision.INTERRUPT,
            reason="tool call requires approval by run policy mode",
            interrupt_reason="approval_required",
        )
    elif allowed and call.tool_name not in allowed:
        outcome = ToolPolicyOutcome(
            decision=ToolPolicyDecision.DENY,
            reason=f"tool '{call.tool_name}' is not in allowed_tools",
        )
    elif call.tool_name in denied:
        outcome = ToolPolicyOutcome(
            decision=ToolPolicyDecision.DENY,
            reason=f"tool '{call.tool_name}' is listed in denied_tools",
        )
    elif (
        policy.max_tool_calls is not None
        and current_tool_calls >= policy.max_tool_calls
    ):
        outcome = ToolPolicyOutcome(
            decision=ToolPolicyDecision.DENY,
            reason="max_tool_calls exceeded by tool policy",
        )
    else:
        force_planning = _evaluate_force_planning(
            policy=policy,
            manifest=manifest,
            call=call,
        )
        if force_planning is not None:
            return force_planning
        threshold = policy.approval_required_for_risk
        if threshold is not None and is_risk_at_or_above(manifest, threshold.value):
            outcome = ToolPolicyOutcome(
                decision=ToolPolicyDecision.INTERRUPT,
                reason=f"tool risk '{manifest.risk.value}' requires approval",
                interrupt_reason="approval_required",
                metadata={"risk_threshold": threshold.value},
            )
    return outcome


__all__ = ["evaluate_tool_policy"]
