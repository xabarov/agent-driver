"""T0: repeated runs (N-run) + baseline-vs-treatment comparison."""

from __future__ import annotations

import pytest

from agent_driver.batch import BatchRunner, InMemoryTrajectoryStore, items_from_prompts
from agent_driver.evals import (
    ComparisonReport,
    MetricSummary,
    RunAggregate,
    compare_aggregates,
    render_comparison,
    run_comparison,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


def _agent(answer: str = "ok"):
    return create_agent(
        provider=FakeProvider(response_text=answer), tools=ToolSet.only()
    )


@pytest.mark.asyncio
async def test_repeats_produces_item_times_repeats_trajectories() -> None:
    store = InMemoryTrajectoryStore()
    runner = BatchRunner(_agent("done"), concurrency=2)
    await runner.run(items_from_prompts(["a", "b"]), store=store, repeats=3)
    trajectories = store.trajectories()
    assert len(trajectories) == 6  # 2 items × 3 repeats
    assert {t.run_index for t in trajectories} == {0, 1, 2}


@pytest.mark.asyncio
async def test_repeats_must_be_positive() -> None:
    runner = BatchRunner(_agent(), concurrency=1)
    with pytest.raises(ValueError):
        await runner.run(items_from_prompts(["a"]), repeats=0)


def test_compare_aggregates_computes_deltas() -> None:
    baseline = RunAggregate(
        total_runs=5,
        success_count=4,
        success_rate=0.8,
        cost_usd=MetricSummary(median=0.10),
        latency_ms=MetricSummary(median=300.0),
        total_tokens=MetricSummary(median=2000.0),
    )
    treatment = RunAggregate(
        total_runs=5,
        success_count=5,
        success_rate=1.0,
        cost_usd=MetricSummary(median=0.06),
        latency_ms=MetricSummary(median=180.0),
        total_tokens=MetricSummary(median=1500.0),
    )
    report = compare_aggregates(baseline, treatment, repeats=5)
    assert report.success_rate_delta == pytest.approx(0.2)
    assert report.cost_usd_median_delta == pytest.approx(-0.04)  # cheaper
    assert report.latency_ms_median_delta == pytest.approx(-120.0)  # faster
    assert report.total_tokens_median_delta == pytest.approx(-500.0)


def test_render_comparison_includes_labels_and_rows() -> None:
    report = compare_aggregates(
        RunAggregate(success_rate=0.8, cost_usd=MetricSummary(median=0.1)),
        RunAggregate(success_rate=1.0, cost_usd=MetricSummary(median=0.05)),
        baseline_label="cache_off",
        treatment_label="cache_on",
        repeats=5,
    )
    text = render_comparison(report)
    assert "cache_off" in text and "cache_on" in text
    assert "success_rate" in text and "cost_usd" in text
    assert "repeats=5" in text


@pytest.mark.asyncio
async def test_run_comparison_end_to_end() -> None:
    items = items_from_prompts(["q1", "q2"])
    report = await run_comparison(
        BatchRunner(_agent("base"), concurrency=2),
        BatchRunner(_agent("treat"), concurrency=2),
        items,
        repeats=3,
        baseline_label="A",
        treatment_label="B",
    )
    assert isinstance(report, ComparisonReport)
    assert report.baseline.total_runs == 6  # 2 items × 3 repeats
    assert report.treatment.total_runs == 6
    assert report.baseline.success_rate == 1.0
    assert report.treatment.success_rate == 1.0
    # Identical fake economics → zero deltas.
    assert report.success_rate_delta == 0.0
