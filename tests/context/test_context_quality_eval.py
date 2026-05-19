"""Context quality eval baseline tests."""

from __future__ import annotations

from agent_driver.evals import (
    build_synthetic_context_quality_fixture,
    compaction_default_gate,
    evaluate_baseline_strategies,
    score_context_quality,
)


def test_synthetic_context_quality_fixture_is_stable() -> None:
    """Synthetic fixture should include expected anchors."""
    fixture = build_synthetic_context_quality_fixture()
    assert fixture.fixture_id == "phase8_synthetic_long_session_v1"
    assert "fact_compaction_audit_keys" in fixture.expected_fact_ids
    assert "planning" in fixture.expected_provenance_sources


def test_score_context_quality_metrics_are_bounded() -> None:
    """All quality metrics should stay within normalized bounds."""
    metrics = score_context_quality(
        expected_fact_ids=["f1", "f2"],
        remembered_fact_ids=["f1"],
        hallucinated_fact_ids=["ghost_fact"],
        expected_provenance_sources=["tool_stdout", "planning"],
        seen_provenance_sources=["tool_stdout"],
        audit={"trim_audit": [], "microcompaction_audit": [], "token_pressure": {}},
        used_tokens_estimate=4000,
        budget_limit=8000,
    )
    for value in metrics.values():
        assert 0.0 <= value <= 1.0


def test_baseline_has_trim_and_micro_strategies() -> None:
    """Offline baseline should compare deterministic strategy variants."""
    baseline = evaluate_baseline_strategies()
    assert "trim_only" in baseline
    assert "trim_plus_microcompaction" in baseline
    assert (
        baseline["trim_plus_microcompaction"]["fact_recall"]
        >= baseline["trim_only"]["fact_recall"]
    )


def test_compaction_default_gate_passes_for_current_baseline() -> None:
    """Phase 8 defaults gate should pass only when compaction is non-regressive."""
    passes, details = compaction_default_gate(evaluate_baseline_strategies())
    assert passes is True
    assert details["failures"] == []


def test_compaction_default_gate_detects_regression() -> None:
    """Gate should fail when compaction strategy degrades recall/hallucination."""
    passes, details = compaction_default_gate(
        {
            "trim_plus_microcompaction": {
                "fact_recall": 0.9,
                "hallucinated_facts": 0.0,
            },
            "session_memory_compaction": {
                "fact_recall": 0.8,
                "hallucinated_facts": 0.1,
            },
            "full_llm_compaction": {
                "fact_recall": 0.9,
                "hallucinated_facts": 0.0,
            },
            "partial_compaction": {
                "fact_recall": 0.9,
                "hallucinated_facts": 0.0,
            },
        }
    )
    assert passes is False
    assert details["failures"]
