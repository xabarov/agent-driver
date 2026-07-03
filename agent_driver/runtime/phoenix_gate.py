"""Phoenix validation-gate helpers for live policy evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def build_phoenix_trace_gate(
    *,
    live_claim: bool,
    span_count: int | None = None,
    trace_ids: list[str] | None = None,
    endpoint: str | None = None,
    project_name: str | None = None,
    reason: str | None = None,
) -> ValidationGateResult:
    """Return Phoenix validation result; 0 spans is failed for live claims."""

    clean_trace_ids = [item for item in (trace_ids or []) if item]
    if not live_claim:
        status = "skipped"
        resolved_reason = reason or "no_live_runtime_or_provider_claim"
    elif span_count is None:
        status = "not_run"
        resolved_reason = reason or "phoenix_span_count_missing"
    elif span_count <= 0:
        status = "failed"
        resolved_reason = reason or "phoenix_zero_spans_for_live_claim"
    else:
        status = "passed"
        resolved_reason = reason or "phoenix_spans_present_for_live_claim"

    return ValidationGateResult(
        gate_id="phoenix_trace",
        status=status,
        reason=resolved_reason,
        redacted_metadata={
            "live_claim": live_claim,
            "span_count": span_count,
            "trace_ids": clean_trace_ids,
            "endpoint": endpoint,
            "project_name": project_name,
        },
    )


def write_phoenix_gate_artifacts(
    output_dir: str | Path,
    *,
    live_claim: bool,
    span_count: int | None = None,
    trace_ids: list[str] | None = None,
    endpoint: str | None = None,
    project_name: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Persist Phoenix gate evidence and return artifact manifest/result."""

    gate = build_phoenix_trace_gate(
        live_claim=live_claim,
        span_count=span_count,
        trace_ids=trace_ids,
        endpoint=endpoint,
        project_name=project_name,
        reason=reason,
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        phoenix_run_ids=trace_ids or None,
    )
    return {
        "gate": gate.model_dump(mode="json"),
        "validation_gates": validation_gates,
        "artifact_manifest": manifest,
    }


__all__ = ["build_phoenix_trace_gate", "write_phoenix_gate_artifacts"]
