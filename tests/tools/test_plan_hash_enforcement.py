"""U5 enforcement (epic 053) — required_plan_hash gates on plan content.

The force-planning gate counts an approval only when the recorded plan's
content hash matches the host-required hash; a materially revised plan is
re-gated (DENY) before any write runs. Without required_plan_hash the gate keeps
its presence-only behaviour (backward compatible).
"""

from __future__ import annotations

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.context import PlanningPolicyInput
from agent_driver.contracts.enums import ToolPolicyDecision
from agent_driver.context.planning import plan_content_hash
from agent_driver.tools.policy.evaluator import (
    _evaluate_force_planning,
    _force_planning_has_approved_plan,
)

_H = plan_content_hash("1. inspect\n2. write")
_OTHER = plan_content_hash("1. inspect\n2. DELETE everything")


def _cfg(**kw) -> PlanningPolicyInput:
    return PlanningPolicyInput(enabled=True, **kw)


def test_required_hash_match_counts_as_approved() -> None:
    cfg = _cfg(approved_plan={"plan_id": "p1", "content_hash": _H}, required_plan_hash=_H)
    assert _force_planning_has_approved_plan(cfg) is True


def test_required_hash_mismatch_is_unapproved() -> None:
    cfg = _cfg(
        approved_plan={"plan_id": "p1", "content_hash": _OTHER}, required_plan_hash=_H
    )
    assert _force_planning_has_approved_plan(cfg) is False


def test_required_hash_without_approved_plan_is_unapproved() -> None:
    # approved=True alone no longer suffices when a content-bound hash is required.
    cfg = _cfg(approved=True, required_plan_hash=_H)
    assert _force_planning_has_approved_plan(cfg) is False


def test_no_required_hash_keeps_presence_only() -> None:
    assert _force_planning_has_approved_plan(_cfg(approved=True)) is True
    assert _force_planning_has_approved_plan(_cfg(approved_plan_id="p1")) is True
    assert _force_planning_has_approved_plan(_cfg()) is False


def _write_manifest() -> ToolManifest:
    return ToolManifest(
        name="file_write",
        description="Write file",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.NEVER,
    )


def _policy(required_hash: str, approved_hash: str) -> ToolPolicyInput:
    return ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        metadata={
            "force_planning": {
                "enabled": True,
                "approved_plan": {"plan_id": "p1", "content_hash": approved_hash},
                "required_plan_hash": required_hash,
            }
        },
    )


def test_evaluator_denies_write_on_revised_plan() -> None:
    outcome = _evaluate_force_planning(
        policy=_policy(required_hash=_H, approved_hash=_OTHER),
        manifest=_write_manifest(),
        call=ToolCall(tool_name="file_write", args={"path": "x", "content": "y"}),
        current_tool_calls=0,
    )
    assert outcome is not None
    assert outcome.decision is ToolPolicyDecision.DENY
    assert "approved plan" in outcome.reason


def test_evaluator_allows_write_when_hash_matches() -> None:
    outcome = _evaluate_force_planning(
        policy=_policy(required_hash=_H, approved_hash=_H),
        manifest=_write_manifest(),
        call=ToolCall(tool_name="file_write", args={"path": "x", "content": "y"}),
        current_tool_calls=0,
    )
    assert outcome is None  # approved → not gated
