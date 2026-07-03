"""Benchmark validation gate tests."""

from __future__ import annotations

import json

from agent_driver.runtime.benchmark_gate import (
    build_benchmark_delta_gate,
    write_benchmark_gate_artifacts,
)


def test_benchmark_gate_skips_without_quality_claim() -> None:
    gate = build_benchmark_delta_gate(benchmark_claim=False, passed=True)

    assert gate.status == "skipped"
    assert gate.reason == "no_quality_cost_latency_claim"


def test_benchmark_gate_fails_pass_without_report_for_quality_claim() -> None:
    gate = build_benchmark_delta_gate(benchmark_claim=True, passed=True)

    assert gate.status == "failed"
    assert gate.reason == "benchmark_report_missing_for_claim"


def test_benchmark_gate_passes_and_persists_reports(tmp_path) -> None:
    result = write_benchmark_gate_artifacts(
        tmp_path / "benchmark",
        benchmark_claim=True,
        passed=True,
        benchmark_json={"quality_delta": 0.0, "policy_decisions": {"warn": 1}},
        benchmark_markdown="# Benchmark\n",
        command="make eval-regression",
    )

    assert result["gate"]["status"] == "passed"
    assert result["validation_gates"]["statuses"]["benchmark_delta"] == "passed"
    root = tmp_path / "benchmark"
    assert json.loads((root / "benchmark_report.json").read_text()) == {
        "quality_delta": 0.0,
        "policy_decisions": {"warn": 1},
    }
    assert (root / "benchmark_report.md").read_text() == "# Benchmark\n"
