"""Strategy comparison runner for context compaction baselines."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.evals.context_quality import evaluate_baseline_strategies


@dataclass(frozen=True, slots=True)
class StrategyComparisonRow:
    """One strategy metrics row for report rendering."""

    strategy: str
    fact_recall: float
    hallucinated_facts: float
    provenance_coverage: float
    budget_efficiency: float


def run_context_compaction_strategy_comparison() -> list[StrategyComparisonRow]:
    """Run offline strategy comparison and produce stable rows."""
    baseline = evaluate_baseline_strategies()
    rows: list[StrategyComparisonRow] = []
    for strategy, metrics in baseline.items():
        rows.append(
            StrategyComparisonRow(
                strategy=strategy,
                fact_recall=float(metrics["fact_recall"]),
                hallucinated_facts=float(metrics["hallucinated_facts"]),
                provenance_coverage=float(metrics["provenance_coverage"]),
                budget_efficiency=float(metrics["budget_efficiency"]),
            )
        )
    return rows


def render_context_compaction_report() -> str:
    """Render simple markdown report for PR notes."""
    rows = run_context_compaction_strategy_comparison()
    header = "| strategy | recall | hallucination | provenance | budget_efficiency |\n"
    separator = "|---|---:|---:|---:|---:|\n"
    body = "".join(
        f"| {row.strategy} | {row.fact_recall:.4f} | {row.hallucinated_facts:.4f} | "
        f"{row.provenance_coverage:.4f} | {row.budget_efficiency:.4f} |\n"
        for row in rows
    )
    return header + separator + body


__all__ = [
    "StrategyComparisonRow",
    "render_context_compaction_report",
    "run_context_compaction_strategy_comparison",
]
