"""Structured policy evaluation for planned tool calls."""

from __future__ import annotations

from agent_driver.contracts.context import PlanningPolicyInput
from agent_driver.contracts.enums import (
    PlanningPolicyMode,
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


def _force_planning_enabled(config: PlanningPolicyInput) -> bool:
    if config.mode in {
        PlanningPolicyMode.OFF,
        PlanningPolicyMode.PROMPT_ONLY,
    }:
        return False
    if config.enabled is False:
        return False
    return config.enabled is True or config.mode != PlanningPolicyMode.REQUIRED_FOR_WRITES


def _force_planning_has_approved_plan(config: PlanningPolicyInput) -> bool:
    if config.approved is True:
        return True
    approved_plan_id = config.approved_plan_id
    if isinstance(approved_plan_id, str) and approved_plan_id.strip():
        return True
    approved_plan = config.approved_plan
    if isinstance(approved_plan, dict):
        plan_id = approved_plan.get("plan_id")
        if isinstance(plan_id, str) and plan_id.strip():
            return True
        return approved_plan.get("approved") is True
    return False


def _force_planning_applies(
    *,
    config: PlanningPolicyInput,
    manifest: ToolManifest,
    call: ToolCall,
    current_tool_calls: int,
) -> bool:
    exempt = (
        set(config.exempt_tools)
        if config.exempt_tools is not None
        else _DEFAULT_FORCE_PLANNING_EXEMPT_TOOLS
    )
    if call.tool_name in exempt:
        return False

    if config.gated_tools is not None and call.tool_name in set(config.gated_tools):
        return True

    if config.mode == PlanningPolicyMode.ALWAYS_FOR_MULTISTEP:
        if config.multistep is True:
            return True
        if (
            config.expected_steps is not None
            and config.expected_steps >= config.step_threshold
        ):
            return True
        return current_tool_calls + 1 >= config.step_threshold

    if config.mode == PlanningPolicyMode.REQUIRED_FOR_RISKY_TOOLS:
        min_risk = str(config.min_risk or ToolRisk.MEDIUM.value).strip()
        try:
            threshold = ToolRisk(min_risk)
        except ValueError:
            threshold = ToolRisk.MEDIUM
        return is_risk_at_or_above(manifest, threshold.value)

    side_effects = (
        set(config.gated_side_effects)
        if config.gated_side_effects is not None
        else _DEFAULT_FORCE_PLANNING_SIDE_EFFECTS
    )
    if manifest.side_effect.value in side_effects:
        return True

    min_risk = config.min_risk
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
    current_tool_calls: int,
) -> ToolPolicyOutcome | None:
    config = PlanningPolicyInput.from_metadata(policy.metadata)
    if config is None or not _force_planning_enabled(config):
        return None
    if _force_planning_has_approved_plan(config):
        return None
    if not _force_planning_applies(
        config=config,
        manifest=manifest,
        call=call,
        current_tool_calls=current_tool_calls,
    ):
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
                "mode": config.mode.value,
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
            current_tool_calls=current_tool_calls,
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
