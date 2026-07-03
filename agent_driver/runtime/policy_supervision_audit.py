"""Audit policy-supervision validation artifacts against epic gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_GATE_ARTIFACTS = {
    "openrouter_live_preflight": "openrouter-live-preflight/validation_gates.json",
    "phoenix_trace": "phoenix-gate/validation_gates.json",
    "playwright_ui": "playwright-gate/validation_gates.json",
    "benchmark_delta": "benchmark-gate/validation_gates.json",
}

_ACCEPTANCE_ITEMS = (
    {
        "item": "H4 live provider",
        "gate_id": "openrouter_live_preflight",
        "required_proof": "OpenRouter-compatible live completion evidence.",
    },
    {
        "item": "I2 Phoenix",
        "gate_id": "phoenix_trace",
        "required_proof": "Phoenix trace ids/spans for a claimed live path.",
    },
    {
        "item": "H5 benchmark",
        "gate_id": "benchmark_delta",
        "required_proof": "Benchmark JSON/Markdown for quality/cost/latency claims.",
    },
    {
        "item": "I3 Playwright",
        "gate_id": "playwright_ui",
        "required_proof": "Playwright screenshots for user-visible policy changes.",
    },
)

_CONDITIONAL_NO_CLAIM_GATES = frozenset({"benchmark_delta", "playwright_ui"})


def audit_policy_supervision_artifacts(
    root_dir: str | Path = ".agent-driver/policy-supervision",
) -> dict[str, Any]:
    """Return acceptance audit rows for policy-supervision artifacts."""

    root = Path(root_dir)
    gates = {gate_id: _read_gate(root, gate_id) for gate_id in _GATE_ARTIFACTS}
    rows = []
    for item in _ACCEPTANCE_ITEMS:
        gate_id = str(item["gate_id"])
        gate = gates.get(gate_id)
        artifact_path = root / _GATE_ARTIFACTS[gate_id]
        manifest = _verify_manifest(artifact_path.parent)
        rows.append(
            {
                "item": item["item"],
                "gate_id": gate_id,
                "required_proof": item["required_proof"],
                "artifact_path": str(artifact_path),
                "status": _acceptance_status(
                    gate,
                    manifest,
                    artifact_exists=artifact_path.is_file(),
                ),
                "gate_status": gate.get("status") if gate else "missing",
                "reason": gate.get("reason") if gate else "validation_gates_missing",
                "redacted_metadata": gate.get("redacted_metadata", {}) if gate else {},
                "manifest": manifest,
            }
        )
    return {
        "status": "passed" if all(_row_is_accepted(row) for row in rows) else "open",
        "rows": rows,
        "redaction": {"safe_by_default": True},
    }


def _row_is_accepted(row: dict[str, Any]) -> bool:
    if row.get("status") == "passed":
        return True
    return (
        row.get("gate_id") in _CONDITIONAL_NO_CLAIM_GATES
        and row.get("status") == "no_claim"
    )


def _read_gate(root: Path, gate_id: str) -> dict[str, Any] | None:
    path = root / _GATE_ARTIFACTS[gate_id]
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    gates = payload.get("gates")
    if not isinstance(gates, list):
        return None
    for row in gates:
        if isinstance(row, dict) and row.get("gate_id") == gate_id:
            return row
    return None


def _acceptance_status(
    gate: dict[str, Any] | None,
    manifest: dict[str, Any],
    *,
    artifact_exists: bool,
) -> str:
    if gate is None and artifact_exists and not manifest["valid"]:
        return "artifact_invalid"
    if gate is None:
        return "missing"
    if not manifest["valid"]:
        return "artifact_invalid"
    gate_status = gate.get("status")
    reason = gate.get("reason")
    if gate_status == "passed":
        return "passed"
    if reason in {"openrouter_api_key_missing", "phoenix_span_count_missing"}:
        return "blocked"
    if gate_status == "skipped":
        return "no_claim"
    if gate_status == "failed":
        return "failed"
    return "not_run"


def _verify_manifest(directory: Path) -> dict[str, Any]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return {
            "valid": False,
            "path": str(manifest_path),
            "reason": "manifest_missing",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "valid": False,
            "path": str(manifest_path),
            "reason": f"manifest_invalid_json:{exc.__class__.__name__}",
        }
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return {
            "valid": False,
            "path": str(manifest_path),
            "reason": "manifest_artifacts_missing",
        }
    checked = []
    for row in artifacts:
        if not isinstance(row, dict):
            return {
                "valid": False,
                "path": str(manifest_path),
                "reason": "manifest_artifact_row_invalid",
            }
        relative_path = row.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            return {
                "valid": False,
                "path": str(manifest_path),
                "reason": "manifest_artifact_path_missing",
            }
        artifact_path = directory / relative_path
        if not artifact_path.is_file():
            return {
                "valid": False,
                "path": str(manifest_path),
                "reason": "manifest_artifact_missing",
                "artifact_path": str(artifact_path),
            }
        data = artifact_path.read_bytes()
        if row.get("size_bytes") != len(data):
            return {
                "valid": False,
                "path": str(manifest_path),
                "reason": "manifest_artifact_size_mismatch",
                "artifact_path": str(artifact_path),
            }
        if row.get("sha256") != hashlib.sha256(data).hexdigest():
            return {
                "valid": False,
                "path": str(manifest_path),
                "reason": "manifest_artifact_sha256_mismatch",
                "artifact_path": str(artifact_path),
            }
        checked.append(
            {
                "artifact_type": row.get("artifact_type"),
                "path": relative_path,
            }
        )
    return {
        "valid": True,
        "path": str(manifest_path),
        "artifact_count": len(checked),
        "artifacts": checked,
    }


__all__ = ["audit_policy_supervision_artifacts"]
