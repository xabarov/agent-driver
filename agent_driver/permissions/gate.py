"""Adapt a :class:`PermissionPolicy` to the runtime ``ToolGate`` seam."""

from __future__ import annotations

from agent_driver.permissions.policy import PermissionDecision, PermissionPolicy
from agent_driver.runtime.tool_gate import (
    ToolGate,
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
    ToolGateResult,
)


def build_permission_gate(policy: PermissionPolicy) -> ToolGate:
    """Build a ``ToolGate`` that enforces ``policy`` on each planned call.

    Pass the result as ``tool_gate=`` to ``agent.run`` / ``session.send``; the
    governed executor consults it after the static tool policy. ``DENY`` blocks
    the call (the model sees the denial and can re-plan); ``ASK`` pauses the run
    for operator approval; ``ALLOW`` is a no-op pass-through.
    """

    async def _gate(context: ToolGateContext) -> ToolGateResult:
        outcome = policy.decide(context.tool_name, context.args)
        if outcome.decision == PermissionDecision.DENY:
            return ToolGateDeny(
                reason=outcome.reason
                or f"permission policy denied {context.tool_name!r}"
            )
        if outcome.decision == PermissionDecision.ASK:
            return ToolGateAsk(
                message=outcome.reason
                or f"Approve {context.tool_name!r}? It may be risky."
            )
        return ToolGateAllow(reason=outcome.reason or None)

    return _gate


__all__ = ["build_permission_gate"]
