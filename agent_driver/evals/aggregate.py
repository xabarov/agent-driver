"""N-run aggregation for low-budget harness comparison (T0).

Turns a list of :class:`Trajectory` (typically the same item run K times, or a
whole suite run K times) into median + 5–95% interval summaries per economic
metric. Stochastic agents need median-of-N, never best-of-N — a single pass can
hide large variance (see docs/testing-plan-2026-06-09.md). Pure, deterministic,
dependency-free so it is fully offline-testable.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from agent_driver.batch.contracts import Trajectory
from agent_driver.contracts.base import ContractModel

_DEFAULT_SUCCESS_STATUSES = ("completed",)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile of ``values`` at ``q`` in [0, 100].

    Matches the common "type 7" definition (numpy default) without the
    dependency. Empty input returns 0.0.
    """
    if not values:
        return 0.0
    if not 0.0 <= q <= 100.0:
        raise ValueError("q must be in [0, 100]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (q / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)


class MetricSummary(ContractModel):
    """Distribution summary for one metric across N runs."""

    n: int = 0
    mean: float = 0.0
    median: float = 0.0
    p5: float = 0.0
    p95: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0

    @staticmethod
    def of(values: Sequence[float]) -> "MetricSummary":
        """Summarize a sequence of values (median + 5–95% interval)."""
        clean = [float(v) for v in values if v is not None]
        if not clean:
            return MetricSummary()
        return MetricSummary(
            n=len(clean),
            mean=sum(clean) / len(clean),
            median=percentile(clean, 50.0),
            p5=percentile(clean, 5.0),
            p95=percentile(clean, 95.0),
            minimum=min(clean),
            maximum=max(clean),
        )


class RunAggregate(ContractModel):
    """Aggregate over a set of runs: success rate + per-metric distributions.

    ``cost_usd`` / ``latency_ms`` summarize only the runs that reported them
    (errored runs may lack cost); ``total_tokens`` covers all runs (0 for
    errors). ``success_rate`` is over every run.
    """

    total_runs: int = 0
    success_count: int = 0
    success_rate: float = 0.0
    cost_usd: MetricSummary = Field(default_factory=MetricSummary)
    latency_ms: MetricSummary = Field(default_factory=MetricSummary)
    total_tokens: MetricSummary = Field(default_factory=MetricSummary)


def aggregate_trajectories(
    trajectories: Sequence[Trajectory],
    *,
    success_statuses: tuple[str, ...] = _DEFAULT_SUCCESS_STATUSES,
) -> RunAggregate:
    """Aggregate trajectories into a :class:`RunAggregate`.

    ``success_statuses`` are the terminal statuses counted as a success (default
    ``("completed",)``).
    """
    runs = list(trajectories)
    if not runs:
        return RunAggregate()
    success = sum(1 for t in runs if t.status in success_statuses)
    return RunAggregate(
        total_runs=len(runs),
        success_count=success,
        success_rate=success / len(runs),
        cost_usd=MetricSummary.of([t.cost_usd for t in runs if t.cost_usd is not None]),
        latency_ms=MetricSummary.of(
            [t.latency_ms for t in runs if t.latency_ms is not None]
        ),
        total_tokens=MetricSummary.of(
            [t.usage.get("total_tokens", 0) for t in runs]
        ),
    )


__all__ = [
    "MetricSummary",
    "RunAggregate",
    "aggregate_trajectories",
    "percentile",
]
