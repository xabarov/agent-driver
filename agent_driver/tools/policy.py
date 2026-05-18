"""Structured policy evaluation for planned tool calls."""

from __future__ import annotations

from agent_driver.contracts.enums import ToolPolicyDecision, ToolPolicyMode
from agent_driver.contracts.tools import (
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyOutcome,
)

_RISK_ORDER = {"low": 1, "medium": 2, "high": 3}


def _is_risk_at_or_above(manifest: ToolManifest, threshold: str) -> bool:
    return _RISK_ORDER[manifest.risk.value] >= _RISK_ORDER[threshold]


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
        threshold = policy.approval_required_for_risk
        if threshold is not None and _is_risk_at_or_above(manifest, threshold.value):
            outcome = ToolPolicyOutcome(
                decision=ToolPolicyDecision.INTERRUPT,
                reason=f"tool risk '{manifest.risk.value}' requires approval",
                interrupt_reason="approval_required",
                metadata={"risk_threshold": threshold.value},
            )
    return outcome
