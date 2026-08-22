"""Host-registered tool-decision hooks (opencode-adoption EPIC-03).

A governance seam modelled on opencode's ``permission.ask`` plugin hook: a host can
register ordered callbacks that see each tool call's resolved policy decision and may
**tighten** it (``allow`` -> ``interrupt`` -> ``deny``) — never loosen past a hard
``deny``, and never bypass the static policy or the dynamic tool gate that ran first.
This lets a consumer (excel-ai / Zion / PentestLens) inject domain governance without
forking the runtime.

Scope: the *decision* seam only. Rewriting a tool's model-facing description/params
(opencode's ``tool.definition`` hook) is a separate catalog-assembly concern, deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agent_driver.contracts.enums.tools import ToolPolicyDecision

if TYPE_CHECKING:
    from agent_driver.contracts.runtime import AgentRunInput
    from agent_driver.contracts.tools import ToolManifest

# Strictness order — a hook may only move the decision UP this ladder.
_RANK: dict[ToolPolicyDecision, int] = {
    ToolPolicyDecision.ALLOW: 0,
    ToolPolicyDecision.INTERRUPT: 1,
    ToolPolicyDecision.DENY: 2,
}


@dataclass(frozen=True, slots=True)
class ToolDecisionHookResult:
    """A hook's verdict. ``decision`` is applied only if strictly tighter than the
    current one; ``reason`` documents the tightening; ``feedback`` (optional) is steering
    text surfaced to the model on the next turn when the call is tightened."""

    decision: ToolPolicyDecision
    reason: str | None = None
    feedback: str | None = None


@runtime_checkable
class ToolDecisionHook(Protocol):
    """Host callback consulted after static policy + the dynamic tool gate resolve a
    call's decision. Return ``None`` to leave it unchanged, or a
    :class:`ToolDecisionHookResult` to tighten it. Must be pure/side-effect-free and
    fast; it runs on the hot per-call path."""

    def __call__(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        manifest: "ToolManifest",
        run_input: "AgentRunInput",
        decision: ToolPolicyDecision,
        reason: str | None,
    ) -> ToolDecisionHookResult | None: ...


def tighten_decision(
    current: ToolPolicyDecision, proposed: ToolPolicyDecision
) -> ToolPolicyDecision:
    """Return the stricter of the two decisions (allow < interrupt < deny)."""
    return proposed if _RANK[proposed] > _RANK[current] else current


def apply_decision_hooks(
    hooks: "tuple[ToolDecisionHook, ...]",
    *,
    tool_name: str,
    args: dict[str, Any],
    manifest: "ToolManifest",
    run_input: "AgentRunInput",
    decision: ToolPolicyDecision,
    reason: str | None,
) -> tuple[ToolPolicyDecision, str | None, str | None]:
    """Run the hooks in order and return the STRICTEST ``(decision, reason, feedback)``.

    Hooks may only tighten (a looser proposal is ignored). A hook that RAISES is
    fail-closed to ``deny`` — a broken governance hook blocks the call, it never silently
    allows it. Each hook sees the decision as tightened so far.
    """
    eff_decision, eff_reason, eff_feedback = decision, reason, None
    for hook in hooks:
        try:
            out = hook(
                tool_name=tool_name,
                args=args,
                manifest=manifest,
                run_input=run_input,
                decision=eff_decision,
                reason=eff_reason,
            )
        except Exception as exc:  # noqa: BLE001 - a broken governance hook must fail closed
            out = ToolDecisionHookResult(
                ToolPolicyDecision.DENY,
                reason=f"decision hook error: {type(exc).__name__}",
            )
        if out is None:
            continue
        tightened = tighten_decision(eff_decision, out.decision)
        if tightened != eff_decision:
            eff_decision = tightened
            eff_reason = out.reason or eff_reason
            eff_feedback = out.feedback or eff_feedback
    return eff_decision, eff_reason, eff_feedback


__all__ = [
    "ToolDecisionHook",
    "ToolDecisionHookResult",
    "apply_decision_hooks",
    "tighten_decision",
]
