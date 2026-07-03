"""Validation gate projection helpers for support bundles and trace summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.policy import ValidationGateResult

_DEFAULT_VALIDATION_GATES = (
    "deterministic_tests",
    "support_bundle_artifact",
    "openrouter_live_preflight",
    "phoenix_trace",
    "playwright_ui",
    "benchmark_delta",
)


def build_validation_gate_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return redaction-safe validation gate evidence from host/run metadata."""

    gates = _validation_gate_results(metadata)
    gates_by_id = {gate.gate_id: gate for gate in gates}
    for gate_id in _DEFAULT_VALIDATION_GATES:
        gates_by_id.setdefault(gate_id, ValidationGateResult(gate_id=gate_id))
    ordered = [
        gates_by_id[gate_id]
        for gate_id in _DEFAULT_VALIDATION_GATES
        if gate_id in gates_by_id
    ]
    ordered.extend(
        gate
        for gate_id, gate in sorted(gates_by_id.items())
        if gate_id not in _DEFAULT_VALIDATION_GATES
    )
    serialized = [gate.model_dump(mode="json") for gate in ordered]
    return {
        "count": len(serialized),
        "statuses": {item["gate_id"]: item["status"] for item in serialized},
        "gates": serialized,
        "redaction": {"safe_by_default": True},
    }


def _validation_gate_results(metadata: dict[str, Any]) -> list[ValidationGateResult]:
    raw = metadata.get("validation_gates") or metadata.get("validation_gate_results")
    rows = raw if isinstance(raw, list) else []
    results: list[ValidationGateResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        results.append(ValidationGateResult.model_validate(row))
    return results


__all__ = ["build_validation_gate_summary"]
