"""Reusable context-quality regression gates for compaction rollouts."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.evals.context_quality import evaluate_baseline_strategies


@dataclass(frozen=True, slots=True)
class ContextQualityGatePolicy:
    """Thresholds that candidate strategies must satisfy."""

    minimum_recall_delta: float = 0.0
    maximum_hallucination_delta: float = 0.0
    minimum_provenance_delta: float = 0.0
    minimum_audit_completeness: float = 1.0


@dataclass(frozen=True, slots=True)
class ContextQualityGateResult:
    """Structured gate decision for deterministic CI/local checks."""

    passed: bool
    baseline_strategy: str
    checked_strategies: tuple[str, ...]
    failures: tuple[str, ...]
    metrics: dict[str, dict[str, float]]


def evaluate_context_quality_regression_gate(
    *,
    metrics: dict[str, dict[str, float]] | None = None,
    policy: ContextQualityGatePolicy | None = None,
    baseline_strategy: str = "trim_plus_microcompaction",
    checked_strategies: tuple[str, ...] = (
        "session_memory_compaction",
        "full_llm_compaction",
        "partial_compaction",
    ),
) -> ContextQualityGateResult:
    """Compare compaction strategies against deterministic baseline metrics."""
    resolved_metrics = metrics or evaluate_baseline_strategies()
    resolved_policy = policy or ContextQualityGatePolicy()
    baseline = resolved_metrics.get(baseline_strategy, {})
    baseline_recall = float(baseline.get("fact_recall", 0.0))
    baseline_hallucination = float(baseline.get("hallucinated_facts", 1.0))
    baseline_provenance = float(baseline.get("provenance_coverage", 0.0))

    failures: list[str] = []
    for strategy in checked_strategies:
        row = resolved_metrics.get(strategy, {})
        if not row:
            failures.append(f"{strategy}: missing strategy metrics")
            continue
        recall = float(row.get("fact_recall", 0.0))
        hallucination = float(row.get("hallucinated_facts", 1.0))
        provenance = float(row.get("provenance_coverage", 0.0))
        audit_completeness = float(row.get("audit_completeness", 0.0))
        if recall < baseline_recall + resolved_policy.minimum_recall_delta:
            failures.append(
                f"{strategy}: recall {recall:.4f} below required "
                f"{baseline_recall + resolved_policy.minimum_recall_delta:.4f}"
            )
        if hallucination > (
            baseline_hallucination + resolved_policy.maximum_hallucination_delta
        ):
            failures.append(
                f"{strategy}: hallucination {hallucination:.4f} above allowed "
                f"{baseline_hallucination + resolved_policy.maximum_hallucination_delta:.4f}"
            )
        if provenance < baseline_provenance + resolved_policy.minimum_provenance_delta:
            failures.append(
                f"{strategy}: provenance {provenance:.4f} below required "
                f"{baseline_provenance + resolved_policy.minimum_provenance_delta:.4f}"
            )
        if audit_completeness < resolved_policy.minimum_audit_completeness:
            failures.append(
                f"{strategy}: audit completeness {audit_completeness:.4f} below "
                f"{resolved_policy.minimum_audit_completeness:.4f}"
            )
    return ContextQualityGateResult(
        passed=not failures,
        baseline_strategy=baseline_strategy,
        checked_strategies=checked_strategies,
        failures=tuple(failures),
        metrics=resolved_metrics,
    )


__all__ = [
    "ContextQualityGatePolicy",
    "ContextQualityGateResult",
    "evaluate_context_quality_regression_gate",
]
