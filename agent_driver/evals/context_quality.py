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
    prompt_messages: tuple[dict[str, str], ...]
    observation_rows: tuple[dict[str, Any], ...]
    planning_events: tuple[dict[str, Any], ...]
    digest_refs: tuple[str, ...]
    artifact_refs: tuple[str, ...]


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
        prompt_messages=(
            {
                "role": "user",
                "content": (
                    "Need a stable context gate. Fact user_pref_no_plan_edits: do not edit plan "
                    "files. Fact user_pref_complete_all_todos: complete all todos in order."
                ),
            },
            {
                "role": "assistant",
                "content": "Acknowledged. I will keep all updates deterministic and traceable.",
            },
        ),
        observation_rows=(
            {
                "id": "obs_1",
                "tool_call_id": "call_1",
                "text_preview": "stdout: retrieval window is 30 minutes.",
                "provenance": {"source": "tool_stdout"},
            },
            {
                "id": "obs_2",
                "tool_call_id": "call_2",
                "text_preview": "stderr: openrouter lane remains opt-in.",
                "provenance": {"source": "tool_stderr"},
            },
        ),
        planning_events=(
            {
                "channel": "planning",
                "text": "fact_planning_update_channel: planning updates are replay-visible",
            },
        ),
        digest_refs=("digest_1",),
        artifact_refs=("artifact_1",),
    )


def evaluate_fixture_retention(
    *,
    fixture: ContextQualityFixture,
    retained_fact_ids: list[str],
    retained_observations: list[dict[str, Any]],
    audit: dict[str, Any],
) -> dict[str, object]:
    """Evaluate fixture retention invariants for deterministic offline tests."""
    expected = set(fixture.expected_fact_ids)
    retained = {item for item in retained_fact_ids if item}
    expected_pairs = {
        str(row.get("tool_call_id"))
        for row in fixture.observation_rows
        if row.get("tool_call_id")
    }
    retained_pairs = {
        str(row.get("tool_call_id"))
        for row in retained_observations
        if isinstance(row, dict) and row.get("tool_call_id")
    }
    orphan_tool_pairs = sorted(expected_pairs - retained_pairs)
    seen_sources = {
        str((row.get("provenance") or {}).get("source"))
        for row in retained_observations
        if isinstance(row, dict) and isinstance(row.get("provenance"), dict)
    }
    required_audit_keys = {
        "token_pressure",
        "trim_audit",
        "microcompaction_audit",
    }
    missing_audit_keys = sorted(required_audit_keys - set(audit))
    return {
        "fact_recall": len(expected & retained) / len(expected) if expected else 1.0,
        "orphan_tool_pairs": orphan_tool_pairs,
        "seen_provenance_sources": sorted(seen_sources),
        "missing_audit_keys": missing_audit_keys,
    }


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
    """Return deterministic baseline for Phase 8 compaction strategies."""
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
    session_memory = score_context_quality(
        expected_fact_ids=expected,
        remembered_fact_ids=[
            "fact_retrieval_window",
            "fact_openrouter_lane_optin",
            "fact_compaction_audit_keys",
            "fact_planning_update_channel",
        ],
        hallucinated_fact_ids=[],
        expected_provenance_sources=expected_sources,
        seen_provenance_sources=["tool_stdout", "tool_stderr", "planning"],
        audit={
            "trim_audit": [{}],
            "microcompaction_audit": [{"kind": "compact"}],
            "token_pressure": {"state": "compact_recommended"},
            "compaction_audit": {"decision": {"mode": "session_memory"}},
        },
        used_tokens_estimate=6200,
        budget_limit=12000,
    )
    full_llm = score_context_quality(
        expected_fact_ids=expected,
        remembered_fact_ids=[
            "fact_retrieval_window",
            "fact_openrouter_lane_optin",
            "fact_compaction_audit_keys",
        ],
        hallucinated_fact_ids=[],
        expected_provenance_sources=expected_sources,
        seen_provenance_sources=["tool_stdout", "planning"],
        audit={
            "trim_audit": [{}],
            "microcompaction_audit": [{"kind": "compact"}],
            "token_pressure": {"state": "blocking"},
            "compaction_audit": {"decision": {"mode": "llm_full"}},
        },
        used_tokens_estimate=6400,
        budget_limit=12000,
    )
    partial = score_context_quality(
        expected_fact_ids=expected,
        remembered_fact_ids=[
            "fact_retrieval_window",
            "fact_compaction_audit_keys",
            "fact_planning_update_channel",
        ],
        hallucinated_fact_ids=[],
        expected_provenance_sources=expected_sources,
        seen_provenance_sources=["tool_stdout", "planning"],
        audit={
            "trim_audit": [{}],
            "microcompaction_audit": [{"kind": "compact"}],
            "token_pressure": {"state": "blocking"},
            "compaction_audit": {"decision": {"mode": "partial"}},
        },
        used_tokens_estimate=5900,
        budget_limit=12000,
    )
    return {
        "trim_only": trim_only,
        "trim_plus_microcompaction": trim_plus_micro,
        "session_memory_compaction": session_memory,
        "full_llm_compaction": full_llm,
        "partial_compaction": partial,
    }


def compaction_default_gate(
    baseline: dict[str, dict[str, float]] | None = None,
) -> tuple[bool, dict[str, object]]:
    """Gate Phase 8 default enablement against trim+micro baseline quality."""
    metrics = baseline or evaluate_baseline_strategies()
    baseline_row = metrics.get("trim_plus_microcompaction", {})
    baseline_recall = float(baseline_row.get("fact_recall", 0.0))
    baseline_hallucination = float(baseline_row.get("hallucinated_facts", 1.0))
    candidate_names = (
        "session_memory_compaction",
        "full_llm_compaction",
        "partial_compaction",
    )
    failures: list[str] = []
    for strategy in candidate_names:
        row = metrics.get(strategy, {})
        recall = float(row.get("fact_recall", 0.0))
        hallucination = float(row.get("hallucinated_facts", 1.0))
        if recall < baseline_recall:
            failures.append(
                f"{strategy}: recall {recall:.4f} below baseline {baseline_recall:.4f}"
            )
        if hallucination > baseline_hallucination:
            failures.append(
                f"{strategy}: hallucination {hallucination:.4f} above baseline {baseline_hallucination:.4f}"
            )
    return (
        len(failures) == 0,
        {
            "baseline_strategy": "trim_plus_microcompaction",
            "checked_strategies": list(candidate_names),
            "failures": failures,
        },
    )


__all__ = [
    "compaction_default_gate",
    "ContextQualityFixture",
    "build_synthetic_context_quality_fixture",
    "evaluate_fixture_retention",
    "evaluate_baseline_strategies",
    "score_context_quality",
]
