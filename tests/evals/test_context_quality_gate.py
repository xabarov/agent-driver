"""Tests for deterministic context-quality regression gate helpers."""

from __future__ import annotations

from agent_driver.evals.context_compaction_runner import (
    ContextQualityGatePolicy,
    render_context_compaction_gate_report,
    run_context_compaction_regression_gate,
)
from agent_driver.evals.context_quality_gate import evaluate_context_quality_regression_gate


def test_context_quality_gate_passes_on_current_baseline() -> None:
    """Current fixture baseline should satisfy default rollout gate."""
    result = run_context_compaction_regression_gate()
    assert result.passed is True
    assert result.baseline_strategy == "trim_plus_microcompaction"
    assert not result.failures


def test_context_quality_gate_can_fail_with_strict_policy() -> None:
    """Strict recall delta should produce deterministic gate failures."""
    result = run_context_compaction_regression_gate(
        policy=ContextQualityGatePolicy(minimum_recall_delta=0.2)
    )
    assert result.passed is False
    assert any("recall" in item for item in result.failures)


def test_context_quality_gate_report_contains_status_header() -> None:
    """Rendered report should include pass/fail status and checked strategies."""
    text = render_context_compaction_gate_report()
    assert text.startswith("Context compaction regression gate:")
    assert "Checked:" in text


def test_context_quality_gate_reports_missing_metrics() -> None:
    """Gate should fail clearly when candidate strategy rows are absent."""
    result = evaluate_context_quality_regression_gate(
        metrics={"trim_plus_microcompaction": {"fact_recall": 0.8}},
    )
    assert result.passed is False
    assert any("missing strategy metrics" in item for item in result.failures)
