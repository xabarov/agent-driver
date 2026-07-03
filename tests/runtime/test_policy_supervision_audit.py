"""Policy-supervision artifact audit tests."""

from __future__ import annotations

import hashlib
import json

from agent_driver.runtime.policy_supervision_audit import (
    audit_policy_supervision_artifacts,
)


def test_policy_supervision_audit_reports_missing_artifacts(tmp_path) -> None:
    result = audit_policy_supervision_artifacts(tmp_path / "missing")

    assert result["status"] == "open"
    assert {row["status"] for row in result["rows"]} == {"missing"}


def test_policy_supervision_audit_classifies_blocked_and_no_claim(tmp_path) -> None:
    root = tmp_path / "policy-supervision"
    _write_gate(
        root,
        "openrouter-live-preflight",
        "openrouter_live_preflight",
        "skipped",
        "openrouter_api_key_missing",
        {"live_requested": True, "api_key_configured": False},
    )
    _write_gate(
        root,
        "phoenix-gate",
        "phoenix_trace",
        "skipped",
        "no_live_runtime_or_provider_claim",
        {"live_claim": False},
    )
    _write_gate(
        root,
        "playwright-gate",
        "playwright_ui",
        "skipped",
        "no_user_visible_policy_claim",
        {"ui_claim": False},
    )
    _write_gate(
        root,
        "benchmark-gate",
        "benchmark_delta",
        "skipped",
        "no_quality_cost_latency_claim",
        {"benchmark_claim": False},
    )

    result = audit_policy_supervision_artifacts(root)

    rows = {row["gate_id"]: row for row in result["rows"]}
    assert result["status"] == "open"
    assert rows["openrouter_live_preflight"]["status"] == "blocked"
    assert rows["phoenix_trace"]["status"] == "no_claim"
    assert rows["playwright_ui"]["status"] == "no_claim"
    assert rows["benchmark_delta"]["status"] == "no_claim"


def test_policy_supervision_audit_passes_when_all_gates_pass(tmp_path) -> None:
    root = tmp_path / "policy-supervision"
    _write_gate(root, "openrouter-live-preflight", "openrouter_live_preflight", "passed")
    _write_gate(root, "phoenix-gate", "phoenix_trace", "passed")
    _write_gate(root, "playwright-gate", "playwright_ui", "passed")
    _write_gate(root, "benchmark-gate", "benchmark_delta", "passed")

    result = audit_policy_supervision_artifacts(root)

    assert result["status"] == "passed"
    assert {row["status"] for row in result["rows"]} == {"passed"}


def test_policy_supervision_audit_accepts_conditional_no_claim_gates(tmp_path) -> None:
    root = tmp_path / "policy-supervision"
    _write_gate(root, "openrouter-live-preflight", "openrouter_live_preflight", "passed")
    _write_gate(root, "phoenix-gate", "phoenix_trace", "passed")
    _write_gate(
        root,
        "playwright-gate",
        "playwright_ui",
        "skipped",
        "no_user_visible_policy_claim",
        {"ui_claim": False},
    )
    _write_gate(
        root,
        "benchmark-gate",
        "benchmark_delta",
        "skipped",
        "no_quality_cost_latency_claim",
        {"benchmark_claim": False},
    )

    result = audit_policy_supervision_artifacts(root)

    rows = {row["gate_id"]: row for row in result["rows"]}
    assert result["status"] == "passed"
    assert rows["openrouter_live_preflight"]["status"] == "passed"
    assert rows["phoenix_trace"]["status"] == "passed"
    assert rows["playwright_ui"]["status"] == "no_claim"
    assert rows["benchmark_delta"]["status"] == "no_claim"


def test_policy_supervision_audit_rejects_invalid_manifest(tmp_path) -> None:
    root = tmp_path / "policy-supervision"
    _write_gate(root, "openrouter-live-preflight", "openrouter_live_preflight", "passed")
    validation_path = (
        root / "openrouter-live-preflight" / "validation_gates.json"
    )
    validation_path.write_text("{}", encoding="utf-8")

    result = audit_policy_supervision_artifacts(root)

    rows = {row["gate_id"]: row for row in result["rows"]}
    assert result["status"] == "open"
    assert rows["openrouter_live_preflight"]["status"] == "artifact_invalid"
    assert rows["openrouter_live_preflight"]["manifest"]["reason"] == (
        "manifest_artifact_size_mismatch"
    )


def _write_gate(
    root,
    dirname: str,
    gate_id: str,
    status: str,
    reason: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    path = root / dirname
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": 1,
        "statuses": {gate_id: status},
        "gates": [
            {
                "gate_id": gate_id,
                "status": status,
                "reason": reason,
                "redacted_metadata": metadata or {},
            }
        ],
        "redaction": {"safe_by_default": True},
    }
    artifact_path = path / "validation_gates.json"
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    data = artifact_path.read_bytes()
    manifest = {
        "artifact_count": 1,
        "artifacts": [
            {
                "artifact_type": "validation_gates",
                "path": "validation_gates.json",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
        "redaction": {"safe_by_default": True},
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
