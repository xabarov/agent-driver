"""Offline context-quality baseline helpers for compaction strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextQualityFixture:
    """Synthetic long-session fixture with expected semantic anchors."""

    fixture_id: str
    expected_fact_ids: tuple[str, ...]
    expected_user_fact_ids: tuple[str, ...]
    expected_provenance_sources: tuple[str, ...]


def build_synthetic_context_quality_fixture() -> ContextQualityFixture:
    """Return stable synthetic baseline fixture."""
    return ContextQualityFixture(
        fixture_id="phase8_synthetic_long_session_v1",
        expected_fact_ids=(
            "fact_retrieval_window",
            "fact_openrouter_lane_optin",
            "fact_compaction_audit_keys",
            "fact_planning_update_channel",
        ),
        expected_user_fact_ids=("user_pref_no_plan_edits", "user_pref_complete_all_todos"),
        expected_provenance_sources=("tool_stdout", "tool_stderr", "planning"),
    )


def score_context_quality(
    *,
    expected_fact_ids: list[str],
    remembered_fact_ids: list[str],
    hallucinated_fact_ids: list[str],
    expected_provenance_sources: list[str],
    seen_provenance_sources: list[str],
    audit: dict[str, Any],
    used_tokens_estimate: int | None,
    budget_limit: int | None,
) -> dict[str, float]:
    """Compute compacted-context quality metrics."""
    expected = set(expected_fact_ids)
    remembered = set(remembered_fact_ids)
    hallucinated = set(hallucinated_fact_ids)
    expected_sources = set(expected_provenance_sources)
    seen_sources = set(seen_provenance_sources)

    fact_recall = len(expected & remembered) / len(expected) if expected else 1.0
    hallucination_rate = (
        len(hallucinated) / len(remembered | hallucinated)
        if (remembered or hallucinated)
        else 0.0
    )
    provenance_coverage = (
        len(expected_sources & seen_sources) / len(expected_sources)
        if expected_sources
        else 1.0
    )

    required_audit_keys = {
        "token_pressure",
        "trim_audit",
        "microcompaction_audit",
    }
    if "compaction_audit" in audit:
        required_audit_keys.add("compaction_audit")
    audit_completeness = len(required_audit_keys & set(audit)) / len(required_audit_keys)

    if used_tokens_estimate is None or budget_limit is None or budget_limit <= 0:
        budget_efficiency = 0.0
    else:
        budget_efficiency = max(
            0.0,
            min(1.0, (budget_limit - used_tokens_estimate) / budget_limit),
        )

    return {
        "fact_recall": round(fact_recall, 4),
        "hallucinated_facts": round(hallucination_rate, 4),
        "provenance_coverage": round(provenance_coverage, 4),
        "audit_completeness": round(audit_completeness, 4),
        "budget_efficiency": round(budget_efficiency, 4),
    }


def evaluate_baseline_strategies() -> dict[str, dict[str, float]]:
    """Return deterministic baseline for trim-only and trim+micro strategies."""
    fixture = build_synthetic_context_quality_fixture()
    expected = list(fixture.expected_fact_ids)
    expected_sources = list(fixture.expected_provenance_sources)

    trim_only = score_context_quality(
        expected_fact_ids=expected,
        remembered_fact_ids=[
            "fact_retrieval_window",
            "fact_compaction_audit_keys",
        ],
        hallucinated_fact_ids=["hallucinated_nonexistent_fact"],
        expected_provenance_sources=expected_sources,
        seen_provenance_sources=["tool_stdout"],
        audit={
            "trim_audit": [{}],
            "microcompaction_audit": [],
            "token_pressure": {"state": "warning"},
        },
        used_tokens_estimate=8300,
        budget_limit=12000,
    )
    trim_plus_micro = score_context_quality(
        expected_fact_ids=expected,
        remembered_fact_ids=[
            "fact_retrieval_window",
            "fact_openrouter_lane_optin",
            "fact_compaction_audit_keys",
        ],
        hallucinated_fact_ids=[],
        expected_provenance_sources=expected_sources,
        seen_provenance_sources=["tool_stdout", "tool_stderr"],
        audit={
            "trim_audit": [{}],
            "microcompaction_audit": [{"kind": "compact"}],
            "token_pressure": {"state": "ok"},
        },
        used_tokens_estimate=7100,
        budget_limit=12000,
    )
    return {
        "trim_only": trim_only,
        "trim_plus_microcompaction": trim_plus_micro,
    }


__all__ = [
    "ContextQualityFixture",
    "build_synthetic_context_quality_fixture",
    "evaluate_baseline_strategies",
    "score_context_quality",
]
