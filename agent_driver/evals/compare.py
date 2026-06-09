"""T0: baseline-vs-treatment harness comparison (one axis at a time).

Runs the same task suite through two configurations — a baseline and a treatment
that differs in exactly one harness dimension (e.g. prompt-cache off vs on,
single vs auxiliary model) — N times each, aggregates with median + interval,
and reports the delta. Per the testing plan, never change more than one axis per
comparison so the delta is attributable.

The runners are built by the caller (two agents differing in one config flag);
this module only orchestrates the repeated runs, aggregation and delta.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from agent_driver.batch.contracts import BatchItem
from agent_driver.batch.runner import BatchRunner
from agent_driver.batch.store import InMemoryTrajectoryStore
from agent_driver.contracts.base import ContractModel
from agent_driver.evals.aggregate import RunAggregate, aggregate_trajectories


class ComparisonReport(ContractModel):
    """Baseline vs treatment aggregates plus median deltas (treatment − baseline).

    Negative ``cost_usd_median_delta`` / ``latency_ms_median_delta`` mean the
    treatment is cheaper / faster; positive ``success_rate_delta`` means it
    succeeds more often.
    """

    baseline_label: str = "baseline"
    treatment_label: str = "treatment"
    repeats: int = 1
    baseline: RunAggregate = Field(default_factory=RunAggregate)
    treatment: RunAggregate = Field(default_factory=RunAggregate)
    success_rate_delta: float = 0.0
    cost_usd_median_delta: float = 0.0
    latency_ms_median_delta: float = 0.0
    total_tokens_median_delta: float = 0.0


def compare_aggregates(
    baseline: RunAggregate,
    treatment: RunAggregate,
    *,
    baseline_label: str = "baseline",
    treatment_label: str = "treatment",
    repeats: int = 1,
) -> ComparisonReport:
    """Build a :class:`ComparisonReport` from two pre-computed aggregates."""
    return ComparisonReport(
        baseline_label=baseline_label,
        treatment_label=treatment_label,
        repeats=repeats,
        baseline=baseline,
        treatment=treatment,
        success_rate_delta=treatment.success_rate - baseline.success_rate,
        cost_usd_median_delta=treatment.cost_usd.median - baseline.cost_usd.median,
        latency_ms_median_delta=treatment.latency_ms.median
        - baseline.latency_ms.median,
        total_tokens_median_delta=treatment.total_tokens.median
        - baseline.total_tokens.median,
    )


async def run_comparison(
    baseline_runner: BatchRunner,
    treatment_runner: BatchRunner,
    items: Iterable[BatchItem],
    *,
    repeats: int = 5,
    baseline_label: str = "baseline",
    treatment_label: str = "treatment",
    success_statuses: tuple[str, ...] = ("completed",),
    max_total_cost_usd: float | None = None,
) -> ComparisonReport:
    """Run both configurations over ``items`` ``repeats`` times and compare.

    ``max_total_cost_usd`` caps each side's spend independently (so the whole
    comparison is bounded by ~2× the ceiling).
    """
    item_list = list(items)
    baseline_store = InMemoryTrajectoryStore()
    treatment_store = InMemoryTrajectoryStore()
    await baseline_runner.run(
        item_list,
        store=baseline_store,
        repeats=repeats,
        max_total_cost_usd=max_total_cost_usd,
    )
    await treatment_runner.run(
        item_list,
        store=treatment_store,
        repeats=repeats,
        max_total_cost_usd=max_total_cost_usd,
    )
    baseline_agg = aggregate_trajectories(
        baseline_store.trajectories(), success_statuses=success_statuses
    )
    treatment_agg = aggregate_trajectories(
        treatment_store.trajectories(), success_statuses=success_statuses
    )
    return compare_aggregates(
        baseline_agg,
        treatment_agg,
        baseline_label=baseline_label,
        treatment_label=treatment_label,
        repeats=repeats,
    )


def render_comparison(report: ComparisonReport) -> str:
    """Render a compact side-by-side delta table (deterministic text)."""

    def _row(name: str, base: float, treat: float, delta: float, fmt: str) -> str:
        base_c = format(base, fmt).rjust(12)
        treat_c = format(treat, fmt).rjust(12)
        delta_c = format(delta, f"+{fmt}").rjust(12)
        return f"{name:<16} {base_c} {treat_c} {delta_c}"

    b, t = report.baseline, report.treatment
    lines = [
        f"Comparison: {report.baseline_label} vs {report.treatment_label} "
        f"(repeats={report.repeats})",
        f"{'metric':<16} {report.baseline_label:>12} {report.treatment_label:>12} "
        f"{'delta':>12}",
        _row(
            "success_rate",
            b.success_rate,
            t.success_rate,
            report.success_rate_delta,
            ".3f",
        ),
        _row(
            "cost_usd (med)",
            b.cost_usd.median,
            t.cost_usd.median,
            report.cost_usd_median_delta,
            ".4f",
        ),
        _row(
            "latency_ms(med)",
            b.latency_ms.median,
            t.latency_ms.median,
            report.latency_ms_median_delta,
            ".1f",
        ),
        _row(
            "tokens (med)",
            b.total_tokens.median,
            t.total_tokens.median,
            report.total_tokens_median_delta,
            ".0f",
        ),
    ]
    return "\n".join(lines)


__all__ = [
    "ComparisonReport",
    "compare_aggregates",
    "render_comparison",
    "run_comparison",
]
