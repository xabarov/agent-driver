"""Helpers for emitting trace-safe runtime decision events."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent_driver.contracts.runtime_decisions import RuntimeDecision


def runtime_decision_payload(
    *,
    run_id: str,
    attempt_id: str,
    seq: int,
    kind: str,
    trigger: str,
    action: str,
    reason: str,
    status: str = "applied",
    goal_id: str | None = None,
    policy_id: str | None = None,
    budget: dict[str, Any] | None = None,
    affected_tools: list[str] | None = None,
    required_evidence: list[str] | None = None,
    observed_evidence: list[str] | None = None,
    product_tags: list[str] | None = None,
    redacted_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a RuntimeDecision payload from already-sanitized fields."""

    decision = RuntimeDecision(
        decision_id=f"dec_{uuid4().hex}",
        run_id=run_id,
        attempt_id=attempt_id,
        seq=seq,
        kind=kind,
        trigger=trigger,
        action=action,
        reason=reason,
        status=status,
        goal_id=goal_id,
        policy_id=policy_id,
        budget=budget or {},
        affected_tools=affected_tools or [],
        required_evidence=required_evidence or [],
        observed_evidence=observed_evidence or [],
        product_tags=product_tags or [],
        redacted_metadata=redacted_metadata or {},
    )
    return decision.model_dump(mode="json")


__all__ = ["runtime_decision_payload"]
