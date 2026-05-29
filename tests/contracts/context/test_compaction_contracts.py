"""Compaction contracts tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    CompactionAudit,
    CompactionDecision,
    CompactionMode,
    CompactionResult,
    CompactionSkipReason,
)
from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
)


def test_compaction_decision_requires_skip_reason_when_ineligible() -> None:
    """Ineligible decision should require explicit skip reason."""
    with pytest.raises(ValidationError):
        CompactionDecision(eligible=False, mode=CompactionMode.NONE)


def test_compaction_decision_requires_mode_when_eligible() -> None:
    """Eligible decision should require non-none mode."""
    with pytest.raises(ValidationError):
        CompactionDecision(
            eligible=True,
            mode=CompactionMode.NONE,
            skip_reason=None,
        )


def test_compaction_result_and_audit_round_trip() -> None:
    """Compaction result and audit should round-trip through JSON."""
    decision = CompactionDecision(
        eligible=False,
        mode=CompactionMode.NONE,
        skip_reason=CompactionSkipReason.NOT_ELIGIBLE,
        metadata={"state": "compact_recommended"},
    )
    result = CompactionResult(
        compaction_id="cmp_1",
        mode=CompactionMode.LLM_FULL,
        success=True,
        model="test-model",
        latency_ms=42,
        input_tokens_estimate=1200,
        output_tokens_estimate=350,
        estimated_cost=0.0012,
        retained_digest_ids=["dig_1"],
        retained_artifact_ids=["art_1"],
    )
    audit = CompactionAudit(
        decision=decision,
        result=result,
        failures=[{"reason": "none"}],
    )
    restored = CompactionAudit.model_validate(audit.model_dump(mode="json"))
    assert restored.result is not None
    assert restored.result.model == "test-model"
    assert restored.decision.skip_reason == CompactionSkipReason.NOT_ELIGIBLE


def test_compaction_metadata_keys_are_stable() -> None:
    """Compaction metadata keys should remain stable constants."""
    assert COMPACTION_DECISION_KEY == "compaction_decision"
    assert COMPACTION_AUDIT_KEY == "compaction_audit"
    assert COMPACTION_RESULT_KEY == "compaction_result"
    assert COMPACTION_FAILURES_KEY == "compaction_failures"
