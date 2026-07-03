"""Benchmark validation-gate helpers for policy quality evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def build_benchmark_delta_gate(
    *,
    benchmark_claim: bool,
    passed: bool | None = None,
    report_present: bool = False,
    command: str | None = None,
    reason: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> ValidationGateResult:
    """Return benchmark validation result for quality/cost/latency claims."""

    if not benchmark_claim:
        status = "skipped"
        resolved_reason = reason or "no_quality_cost_latency_claim"
    elif passed is None:
        status = "not_run"
        resolved_reason = reason or "benchmark_result_missing"
    elif not passed:
        status = "failed"
        resolved_reason = reason or "benchmark_delta_failed"
    elif not report_present:
        status = "failed"
        resolved_reason = reason or "benchmark_report_missing_for_claim"
    else:
        status = "passed"
        resolved_reason = reason or "benchmark_delta_passed_with_report"

    return ValidationGateResult(
        gate_id="benchmark_delta",
        status=status,
        reason=resolved_reason,
        command=command,
        redacted_metadata={
            "benchmark_claim": benchmark_claim,
            "report_present": report_present,
            "metrics": metrics or {},
        },
    )


def write_benchmark_gate_artifacts(
    output_dir: str | Path,
    *,
    benchmark_claim: bool,
    passed: bool | None = None,
    benchmark_json: dict[str, Any] | None = None,
    benchmark_markdown: str | None = None,
    command: str | None = None,
    reason: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist benchmark gate evidence and return artifact manifest/result."""

    report_present = benchmark_json is not None or benchmark_markdown is not None
    gate = build_benchmark_delta_gate(
        benchmark_claim=benchmark_claim,
        passed=passed,
        report_present=report_present,
        command=command,
        reason=reason,
        metrics=metrics,
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        benchmark_json=benchmark_json,
        benchmark_markdown=benchmark_markdown,
    )
    return {
        "gate": gate.model_dump(mode="json"),
        "validation_gates": validation_gates,
        "artifact_manifest": manifest,
    }


__all__ = ["build_benchmark_delta_gate", "write_benchmark_gate_artifacts"]
