"""Pure required-evidence policy-gate predicates (extracted from steps.py).

Leaf module: the finalize-time required-evidence block computation and its
policy/transaction/workbook-context predicates. Read ``context``/metadata only —
no self, no host, no in-module back-edge — so the import stays one-way
(steps -> evidence_policy).
"""

from __future__ import annotations
from agent_driver.observability.provenance import build_provenance_summary
from agent_driver.runtime.policy import policy_profile_from_metadata
from agent_driver.runtime.single_agent.types import (
    RunContext,
)


def _required_policy_evidence_block(
    context: RunContext,
    *,
    events: list[dict[str, object]],
) -> dict[str, object] | None:
    profile = policy_profile_from_metadata(context.run_input.app_metadata)
    if profile is None or profile.mode not in {"enforce", "fail_closed"}:
        return None
    enabled = set(profile.enabled_policy_ids)
    required = set(profile.required_evidence)
    if not required:
        return None
    metadata = {**context.run_input.app_metadata, **context.metadata}
    provenance = build_provenance_summary(
        events=events,
        metadata=metadata,
        required_evidence=list(required),
    )
    verdicts = provenance.get("contract_verdicts", {})
    violations = verdicts.get("violations") if isinstance(verdicts, dict) else {}
    if not isinstance(violations, dict):
        return None
    if (
        "source_evidence" in required
        and _policy_enabled(enabled, "required_source_evidence")
        and violations.get("missing_source_evidence") is True
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="required_source_evidence",
            action="mark_blocked",
            reason="required_source_evidence_missing",
            required_evidence=["source_evidence"],
        )
    if (
        "workbook_context" in required
        and _policy_enabled(enabled, "workbook_context_required")
        and not _workbook_context_observed(metadata)
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="workbook_context_required",
            action="mark_blocked",
            reason="required_workbook_context_missing",
            required_evidence=["workbook_context"],
        )
    if (
        "artifact_provenance" in required
        and _policy_enabled(enabled, "artifact_provenance_required")
        and violations.get("missing_artifact_provenance") is True
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="artifact_provenance_required",
            action="mark_blocked",
            reason="required_artifact_provenance_missing",
            required_evidence=["artifact_provenance"],
        )
    if (
        _policy_enabled(enabled, "side_effect_transaction_required")
        and _transaction_policy_enabled(profile.side_effect_rules, required)
        and violations.get("unsafe_side_effect_without_transaction_projection") is True
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="side_effect_transaction_required",
            action="rollback",
            reason="side_effect_transaction_missing",
            required_evidence=["side_effect_transactions"],
            redacted_metadata={
                "rollback_available": False,
                "rollback_projection": "missing",
            },
        )
    return None


def _policy_enabled(enabled: set[str], policy_id: str) -> bool:
    return not enabled or policy_id in enabled


def _transaction_policy_enabled(
    side_effect_rules: dict[str, object],
    required_evidence: set[str],
) -> bool:
    if "side_effect_transactions" in required_evidence:
        return True
    return side_effect_rules.get("require_transaction_projection") is True


def _workbook_context_observed(metadata: dict[str, object]) -> bool:
    workbook_context = metadata.get("workbook_context")
    if isinstance(workbook_context, dict):
        return True
    if isinstance(workbook_context, list) and any(
        isinstance(item, dict) for item in workbook_context
    ):
        return True
    rows = metadata.get("context_provenance")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind") or row.get("type")
        if kind in {"workbook", "workbook_context"}:
            return True
    return False


def _evidence_block(
    *,
    profile_id: str,
    mode: str,
    policy_id: str,
    action: str,
    reason: str,
    required_evidence: list[str],
    redacted_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "policy_id": policy_id,
        "policy_profile_id": profile_id,
        "policy_mode": mode,
        "action": action,
        "reason": reason,
        "required_evidence": required_evidence,
        "selected_policy_action": action,
        "enforcement": policy_id,
        **dict(redacted_metadata or {}),
    }
