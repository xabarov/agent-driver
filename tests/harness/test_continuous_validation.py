"""Continuous-validation audit tests for persisted capability-pack evidence."""

from __future__ import annotations

import json
import shlex
import sys
from datetime import datetime, timezone

import pytest

from agent_driver.contracts.continuous_validation import (
    HarnessBaseline,
    ValidationRunRecord,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.harness import (
    audit_validation_evidence,
    run_capability_pack_deterministic_gates,
    seed_flake_records,
    seed_harness_baselines,
    seed_host_adoption_states,
    seed_release_gate_policies,
)
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def _write_run_artifacts(tmp_path, *, pack_id: str, scenario_id: str):
    command = f"{shlex.quote(sys.executable)} -c \"print('validation ok')\""
    payload = run_capability_pack_deterministic_gates(
        pack_id=pack_id,
        scenario_ids=[scenario_id],
        deterministic_commands=[command],
        support_bundle_artifact_path="manifest.json",
        cwd=tmp_path,
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gate_results": payload["validation_gate_results"]}
    )
    write_validation_artifacts(
        tmp_path,
        evidence_index=payload["evidence_index"],
        validation_gates=validation_gates,
        command_outputs=payload["executed_commands"],
        extra_json_artifacts={
            "capability_pack_resolution": payload["capability_pack_resolution"],
            "capability_pack_run": payload,
        },
    )
    return payload


def test_continuous_validation_contracts_validate_seed_profiles() -> None:
    baselines = seed_harness_baselines()
    policies = seed_release_gate_policies()
    adoption = seed_host_adoption_states()

    assert set(baselines) == {
        "excel_workbook_chat.baseline.v1",
        "deep_research_chat_demo.baseline.v1",
    }
    assert (
        baselines["excel_workbook_chat.baseline.v1"].expected_gate_statuses[
            "openrouter_live_preflight"
        ]
        == "no_claim"
    )
    assert policies["provider_behavior_change"].live_required_gate_ids == [
        "openrouter_live_preflight",
        "phoenix_trace",
    ]
    assert (
        "provider_catalog.sanitizer_matrix.v1"
        in policies["provider_catalog_contract_change"].required_gate_ids
    )
    assert policies["provider_catalog_contract_change"].live_required_gate_ids == [
        "phoenix_trace"
    ]
    assert (
        "benchmark_delta"
        in policies["provider_catalog_contract_change"].optional_gate_ids
    )
    assert policies["adapter_protocol_change"].change_types == [
        "adapter_protocol",
        "protocol_adapter",
        "stream_projection",
    ]
    assert policies["adapter_protocol_change"].required_gate_ids == [
        "deterministic_tests",
        "support_bundle_artifact",
    ]
    assert policies["adapter_ui_projection_change"].ui_required_gate_ids == [
        "playwright_ui"
    ]
    assert policies["adapter_live_runtime_claim"].live_required_gate_ids == [
        "openrouter_live_preflight",
        "phoenix_trace",
    ]
    assert policies["lifecycle_hook_api_change"].change_types == [
        "lifecycle_hook_api",
        "middleware",
        "hook_contract",
        "enforce_mode",
    ]
    assert policies["lifecycle_hook_api_change"].required_gate_ids == [
        "deterministic_tests",
        "support_bundle_artifact",
    ]
    assert policies["durable_lifecycle_contract_change"].required_gate_ids == [
        "deterministic_tests",
        "support_bundle_artifact",
    ]
    assert policies["durable_lifecycle_resume_claim"].live_required_gate_ids == [
        "phoenix_trace"
    ]
    assert policies["skills_lifecycle_contract_change"].required_gate_ids == [
        "deterministic_tests",
        "support_bundle_artifact",
        "skills_lifecycle.inventory_lock_diff.v1",
        "skills_lifecycle.selection_evidence.v1",
    ]
    assert "skill_contract" in policies["skills_lifecycle_contract_change"].change_types
    assert adoption["excel_ai:excel_workbook_chat"].behavior_change_enabled is False


def test_continuous_validation_contracts_reject_secret_values() -> None:
    with pytest.raises(ValueError, match="must not contain secret values"):
        HarnessBaseline(
            baseline_id="bad",
            pack_id="excel_workbook_chat",
            product_adapter_id="excel_ai",
            redacted_metadata={"api_key": "sk-live-value"},
        )


def test_validation_run_rejects_passed_gate_without_artifact_ref() -> None:
    with pytest.raises(ValueError, match="passed validation gates"):
        ValidationRunRecord(
            run_id="missing-artifact",
            gate_results=[
                ValidationGateResult(gate_id="deterministic_tests", status="passed")
            ],
        )


def test_audit_validation_evidence_reports_no_claim_live_gates(tmp_path) -> None:
    _write_run_artifacts(
        tmp_path,
        pack_id="excel_workbook_chat",
        scenario_id="excel.workbook_context.transaction.v1",
    )

    payload = audit_validation_evidence([tmp_path], strict=True, no_live=True)

    assert payload["strict_passed"] is True
    assert payload["regression_summary"]["candidate_status"] == "no_claim"
    assert payload["validation_run"]["pack_ids"] == ["excel_workbook_chat"]
    assert payload["validation_run"]["scenario_ids"] == [
        "excel.workbook_context.transaction.v1"
    ]
    statuses = {
        gate["gate_id"]: gate["status"]
        for gate in payload["validation_run"]["gate_results"]
    }
    assert statuses["deterministic_tests"] == "passed"
    assert statuses["support_bundle_artifact"] == "passed"
    assert statuses["openrouter_live_preflight"] == "no_claim"
    assert "openrouter_live_preflight" in payload["dashboard_summary"]["no_claim_gates"]
    assert "Validation Audit" in payload["markdown_report"]


def test_audit_validation_evidence_flags_missing_required_deterministic_gates(
    tmp_path,
) -> None:
    from agent_driver.harness import build_capability_pack_dry_run

    payload = build_capability_pack_dry_run(
        pack_id="deep_research_chat_demo",
        scenario_ids=["chat_demo.deep_research.source_report.v1"],
    )
    write_validation_artifacts(
        tmp_path,
        evidence_index=payload["evidence_index"],
        extra_json_artifacts={
            "capability_pack_resolution": payload["capability_pack_resolution"],
            "capability_pack_dry_run": payload,
        },
    )

    audit = audit_validation_evidence([tmp_path], strict=True, no_live=True)

    assert audit["strict_passed"] is False
    assert audit["regression_summary"]["candidate_status"] == "failed"
    assert audit["regression_summary"]["skipped_required_gates"] == [
        "deterministic_tests",
        "support_bundle_artifact",
    ]


def test_audit_validation_evidence_flags_corrupt_manifest_artifacts(tmp_path) -> None:
    _write_run_artifacts(
        tmp_path,
        pack_id="deep_research_chat_demo",
        scenario_id="chat_demo.deep_research.source_report.v1",
    )
    validation_gates_path = tmp_path / "validation_gates.json"
    data = json.loads(validation_gates_path.read_text(encoding="utf-8"))
    data["count"] = 999
    validation_gates_path.write_text(
        json.dumps(data, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    payload = audit_validation_evidence([tmp_path], strict=True, no_live=True)

    assert payload["strict_passed"] is False
    assert payload["regression_summary"]["candidate_status"] == "failed"
    assert "validation_gates.json" in payload["regression_summary"]["corrupt_artifacts"]


def test_audit_validation_evidence_keeps_quarantined_gate_visible(tmp_path) -> None:
    _write_run_artifacts(
        tmp_path,
        pack_id="deep_research_chat_demo",
        scenario_id="chat_demo.deep_research.source_report.v1",
    )
    flake = seed_flake_records()["example.playwright_ui.quarantine"]

    payload = audit_validation_evidence(
        [tmp_path],
        strict=True,
        no_live=True,
        flake_records=[flake],
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )

    statuses = {
        gate["gate_id"]: gate["status"]
        for gate in payload["validation_run"]["gate_results"]
    }
    assert statuses["playwright_ui"] == "quarantined"
    assert payload["regression_summary"]["candidate_status"] == "no_claim"
