"""Deterministic capability-pack fixture and resolver tests."""

from __future__ import annotations

import shlex
import sys

import pytest

from agent_driver.contracts.capability_packs import HarnessCapabilityPack
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.harness import (
    build_capability_pack_dry_run,
    resolve_capability_pack,
    run_capability_pack_deterministic_gates,
    seed_adapter_manifests,
    seed_capability_packs,
    seed_scenario_specs,
)


def test_seed_capability_packs_validate_two_products() -> None:
    packs = seed_capability_packs()

    assert set(packs) == {"excel_workbook_chat", "deep_research_chat_demo"}
    assert packs["excel_workbook_chat"].target_product_family == "excel_ai"
    assert packs["deep_research_chat_demo"].required_evidence == [
        "source_evidence",
        "artifact_provenance",
        "validation_gates",
    ]
    assert packs["excel_workbook_chat"].ownership_notes
    assert packs["deep_research_chat_demo"].review_checklist
    assert {gate.gate_id for gate in packs["excel_workbook_chat"].release_gates} >= {
        "deterministic_tests",
        "support_bundle_artifact",
        "openrouter_live_preflight",
        "phoenix_trace",
        "playwright_ui",
        "benchmark_delta",
    }


def test_provider_catalog_scenarios_are_seeded() -> None:
    scenarios = seed_scenario_specs()

    assert {
        "provider_catalog.plugin_registry.v1",
        "provider_catalog.sanitizer_matrix.v1",
        "provider_catalog.openrouter_preflight.v1",
        "provider_catalog.excel_workbook_routes.v1",
        "provider_catalog.chat_demo_research_routes.v1",
    } <= set(scenarios)
    assert scenarios["provider_catalog.sanitizer_matrix.v1"].artifact_paths == [
        "provider_sanitizer_matrix.json",
        "provider_compatibility_report.json",
    ]


def test_skill_lifecycle_scenarios_are_seeded() -> None:
    scenarios = seed_scenario_specs()

    assert {
        "skills_lifecycle.inventory_lock_diff.v1",
        "skills_lifecycle.selection_evidence.v1",
        "skills_lifecycle.invocation_provenance.v1",
        "skills_lifecycle.excel_workbook_skills.v1",
        "skills_lifecycle.chat_demo_research_skills.v1",
    } <= set(scenarios)
    assert scenarios["skills_lifecycle.selection_evidence.v1"].required_evidence == [
        "skill_selection_evidence",
        "skill_lifecycle_compatibility_report",
        "redaction_validation",
    ]
    assert (
        "playwright_ui"
        in scenarios["skills_lifecycle.excel_workbook_skills.v1"].optional_live_gate_ids
    )


def test_capability_pack_rejects_secret_values() -> None:
    with pytest.raises(ValueError, match="must not contain secret values"):
        HarnessCapabilityPack(
            pack_id="bad",
            version="0.1.0",
            target_product_family="test",
            provider_route_requirements={"api_key": "sk-live secret value"},
        )


def test_capability_pack_allows_secret_env_var_names() -> None:
    pack = HarnessCapabilityPack(
        pack_id="ok",
        version="0.1.0",
        target_product_family="test",
        provider_route_requirements={"api_key": "OPENROUTER_API_KEY"},
    )

    assert pack.provider_route_requirements["api_key"] == "OPENROUTER_API_KEY"


def test_excel_pack_resolution_records_skipped_live_gates() -> None:
    packs = seed_capability_packs()
    adapters = seed_adapter_manifests()
    scenarios = seed_scenario_specs()

    resolution = resolve_capability_pack(
        packs["excel_workbook_chat"],
        adapter_manifest=adapters["excel_ai"],
        scenario_specs=[scenarios["excel.workbook_context.transaction.v1"]],
        gate_results=[
            ValidationGateResult(
                gate_id="deterministic_tests",
                status="passed",
                evidence_path="artifacts/capability-pack-tests.txt",
            )
        ],
    )

    dumped = resolution.model_dump(mode="json")
    assert dumped["pack_id"] == "excel_workbook_chat"
    assert dumped["adapter_id"] == "excel_ai"
    assert dumped["gate_statuses"]["deterministic_tests"] == "passed"
    assert dumped["gate_statuses"]["openrouter_live_preflight"] == "skipped"
    assert dumped["gate_statuses"]["phoenix_trace"] == "skipped"
    assert dumped["gate_statuses"]["benchmark_delta"] == "skipped"
    assert "context_provenance" in dumped["required_evidence"]
    assert dumped["evidence_index"]["skipped_gate_ids"] == [
        "benchmark_delta",
        "openrouter_live_preflight",
        "phoenix_trace",
        "playwright_ui",
    ]
    assert dumped["redacted_metadata"]["no_runtime_behavior_change"] is True


def test_chat_demo_resolution_includes_adapter_commands_without_secrets() -> None:
    packs = seed_capability_packs()
    adapters = seed_adapter_manifests()
    scenarios = seed_scenario_specs()

    resolution = resolve_capability_pack(
        packs["deep_research_chat_demo"],
        adapter_manifest=adapters["chat_demo"],
        scenario_specs=[scenarios["chat_demo.deep_research.source_report.v1"]],
    )
    dumped = resolution.model_dump(mode="json")

    assert dumped["pack_id"] == "deep_research_chat_demo"
    assert dumped["scenario_ids"] == ["chat_demo.deep_research.source_report.v1"]
    assert "source_evidence" in dumped["required_evidence"]
    assert any(
        "trace-summary" in item for item in adapters["chat_demo"].trace_endpoints
    )
    assert all(
        "API_KEY=" not in command for command in dumped["optional_live_commands"]
    )
    assert not any(
        "<" in command and ">" in command
        for command in dumped["deterministic_commands"]
    )


def test_seed_adapter_manifests_use_concrete_non_secret_command_references() -> None:
    for adapter in seed_adapter_manifests().values():
        command_fields = (
            adapter.deterministic_commands
            + adapter.optional_live_commands
            + adapter.benchmark_commands
            + adapter.artifact_output_paths
            + adapter.playwright_specs
        )
        assert all("API_KEY=" not in item for item in command_fields)
        assert all("<" not in item and ">" not in item for item in command_fields)
        assert all("/mnt/share/" not in item for item in command_fields)
    for scenario in seed_scenario_specs().values():
        assert all("API_KEY=" not in item for item in scenario.artifact_paths)
        assert all(
            "<" not in item and ">" not in item for item in scenario.artifact_paths
        )


def test_capability_pack_dry_run_payload_is_execution_free() -> None:
    payload = build_capability_pack_dry_run(
        pack_id="deep_research_chat_demo",
        scenario_ids=["chat_demo.deep_research.source_report.v1"],
    )

    assert payload["mode"] == "dry_run"
    assert payload["executed_commands"] == []
    assert payload["redaction"]["contains_raw_command_output"] is False
    resolution = payload["capability_pack_resolution"]
    assert resolution["pack_id"] == "deep_research_chat_demo"
    assert resolution["gate_statuses"]["openrouter_live_preflight"] == "skipped"
    assert payload["evidence_index"]["skipped_gate_ids"]


def test_capability_pack_dry_run_rejects_wrong_adapter_scenario() -> None:
    with pytest.raises(ValueError, match="belongs to adapter"):
        build_capability_pack_dry_run(
            pack_id="deep_research_chat_demo",
            adapter_id="chat_demo",
            scenario_ids=["excel.workbook_context.transaction.v1"],
        )


def test_run_deterministic_gates_executes_concrete_command(tmp_path) -> None:
    command = f"{shlex.quote(sys.executable)} -c \"print('capability ok')\""

    payload = run_capability_pack_deterministic_gates(
        pack_id="deep_research_chat_demo",
        scenario_ids=["chat_demo.deep_research.source_report.v1"],
        deterministic_commands=[command],
        cwd=tmp_path,
    )

    assert payload["mode"] == "run_deterministic"
    assert payload["executed_commands"][0]["status"] == "passed"
    assert payload["executed_commands"][0]["stdout"].strip() == "capability ok"
    assert payload["validation_gate_results"][0]["status"] == "passed"
    resolution = payload["capability_pack_resolution"]
    assert resolution["gate_statuses"]["deterministic_tests"] == "passed"


def test_run_deterministic_gates_blocks_template_placeholders(tmp_path) -> None:
    payload = run_capability_pack_deterministic_gates(
        pack_id="excel_workbook_chat",
        scenario_ids=["excel.workbook_context.transaction.v1"],
        deterministic_commands=["python -m pytest backend/tests/<test>.py"],
        cwd=tmp_path,
    )

    assert payload["executed_commands"][0]["status"] == "blocked"
    assert payload["executed_commands"][0]["reason"] == (
        "command_template_contains_placeholder"
    )
    assert payload["validation_gate_results"][0]["status"] == "blocked"
