"""T0: N-run aggregation — percentile math + RunAggregate over trajectories."""

from __future__ import annotations

import pytest

from agent_driver.batch.contracts import Trajectory
from agent_driver.evals import (
    MetricSummary,
    RunAggregate,
    aggregate_trajectories,
    percentile,
)


def test_percentile_linear_interpolation() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 100.0) == 4.0
    assert percentile(values, 50.0) == 2.5  # interpolated median
    assert percentile([], 50.0) == 0.0
    assert percentile([7.0], 95.0) == 7.0


def test_percentile_rejects_out_of_range_q() -> None:
    with pytest.raises(ValueError):
        percentile([1.0], 101.0)


def test_metric_summary_of_values() -> None:
    s = MetricSummary.of([10.0, 20.0, 30.0, 40.0, 50.0])
    assert s.n == 5
    assert s.median == 30.0
    assert s.minimum == 10.0
    assert s.maximum == 50.0
    assert s.mean == 30.0
    # 5th/95th percentile interpolate within the range.
    assert 10.0 <= s.p5 <= 20.0
    assert 40.0 <= s.p95 <= 50.0


def test_metric_summary_empty_is_zeroed() -> None:
    s = MetricSummary.of([])
    assert s.n == 0 and s.median == 0.0 and s.mean == 0.0


def _traj(
    *,
    status: str = "completed",
    cost: float | None = None,
    latency: float | None = None,
    tokens: int = 0,
    run_index: int = 0,
) -> Trajectory:
    return Trajectory(
        item_id="i",
        run_id="r",
        status=status,
        run_index=run_index,
        cost_usd=cost,
        latency_ms=latency,
        usage={"total_tokens": tokens},
    )


def test_aggregate_success_rate_and_metrics() -> None:
    runs = [
        _traj(status="completed", cost=0.01, latency=100.0, tokens=1000, run_index=0),
        _traj(status="completed", cost=0.03, latency=300.0, tokens=3000, run_index=1),
        _traj(status="failed", cost=0.02, latency=200.0, tokens=2000, run_index=2),
    ]
    agg = aggregate_trajectories(runs)
    assert agg.total_runs == 3
    assert agg.success_count == 2
    assert agg.success_rate == pytest.approx(2 / 3)
    assert agg.cost_usd.median == 0.02
    assert agg.latency_ms.median == 200.0
    assert agg.total_tokens.median == 2000.0


def test_aggregate_ignores_missing_cost_latency() -> None:
    runs = [
        _traj(cost=None, latency=None, tokens=10),
        _traj(cost=0.05, latency=50.0, tokens=20),
    ]
    agg = aggregate_trajectories(runs)
    # Only the run that reported cost/latency counts toward those summaries.
    assert agg.cost_usd.n == 1 and agg.cost_usd.median == 0.05
    assert agg.latency_ms.n == 1
    # Tokens cover every run.
    assert agg.total_tokens.n == 2


def test_aggregate_empty_is_zeroed() -> None:
    agg = aggregate_trajectories([])
    assert agg == RunAggregate()


def test_custom_success_statuses() -> None:
    runs = [_traj(status="paused"), _traj(status="completed")]
    agg = aggregate_trajectories(runs, success_statuses=("completed", "paused"))
    assert agg.success_rate == 1.0
