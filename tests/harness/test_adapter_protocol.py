"""Harness adapter protocol projection and compatibility tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_driver.contracts import RuntimeEventType, new_runtime_event
from agent_driver.contracts.capability_packs import (
    EvidenceArtifactIndex,
    EvidenceArtifactRef,
)
from agent_driver.contracts.harness_adapter import (
    HarnessAdapterControl,
    HarnessAdapterEvent,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.harness import (
    audit_validation_evidence,
    build_harness_adapter_compatibility_report,
    project_harness_adapter_events,
    seed_harness_adapter_compatibility_reports,
    seed_scenario_specs,
    write_harness_adapter_compatibility_artifacts,
)
from agent_driver.runtime.stream import project_runtime_events
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def _stream_events():
    return project_runtime_events(
        [
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={"run_id": "run_adapter", "attempt_id": "att_1", "seq": 1},
                options={
                    "payload": {
                        "session_id": "session_1",
                        "adapter_id": "chat_demo",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.TOKEN_DELTA,
                context={"run_id": "run_adapter", "attempt_id": "att_1", "seq": 2},
                options={"payload": {"delta_text": "hello sk-secretsecretsecret"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.INTERRUPT_REQUESTED,
                context={"run_id": "run_adapter", "attempt_id": "att_1", "seq": 3},
                options={
                    "payload": {
                        "interrupt_id": "approval_1",
                        "tool_name": "file_write",
                        "tool_call_id": "tc1",
                        "side_effect_class": "write",
                        "args_summary": "write report.md",
                        "allowed_actions": ["approve", "reject"],
                        "api_key": "sk-should-not-leak",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.ARTIFACT_CREATED,
                context={"run_id": "run_adapter", "attempt_id": "att_1", "seq": 4},
                options={
                    "payload": {
                        "artifact_id": "research/report.md",
                        "kind": "report",
                        "path": "research/report.md",
                        "tool_call_id": "tc1",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": "run_adapter", "attempt_id": "att_1", "seq": 5},
            ),
        ]
    )


def test_project_harness_adapter_events_have_stable_cursors_and_redaction() -> None:
    rows = project_harness_adapter_events(
        _stream_events(), session_id="session_1", source="replay"
    )

    assert [row.cursor for row in rows] == [
        "run_adapter:1",
        "run_adapter:2",
        "run_adapter:3",
        "run_adapter:4",
        "run_adapter:5",
    ]
    assert rows[1].display["summary"] == "hello <redacted>"
    approval_rows = [row for row in rows if row.approval_request is not None]
    assert approval_rows[0].approval_request.request_id == "approval_1"
    assert approval_rows[0].approval_request.response_options == ["approve", "reject"]
    assert approval_rows[0].redacted_metadata["diagnostics"]["event"] == (
        "interrupt_requested"
    )
    assert rows[3].artifact_refs[0].artifact_type == "report"
    assert rows[3].artifact_refs[0].path == "research/report.md"


def test_harness_adapter_contracts_reject_secret_metadata_and_bad_cursor() -> None:
    with pytest.raises(ValueError, match="must not contain secret values"):
        HarnessAdapterControl(
            control_id="c1",
            control_kind="abort",
            payload={"api_key": "sk-live-value"},
        )

    with pytest.raises(ValueError, match="cursor must be stable"):
        HarnessAdapterEvent(
            event_id="bad",
            run_id="run_1",
            attempt_id="att_1",
            cursor="wrong",
            seq=1,
            kind="run_started",
            category="lifecycle",
            state="started",
        )


def test_build_harness_adapter_compatibility_report_truthful_no_live_statuses() -> None:
    evidence_index = EvidenceArtifactIndex(
        index_id="adapter-chat-demo",
        pack_id="deep_research_chat_demo",
        scenario_ids=["harness_adapter.chat_demo.deep_research.v1"],
        gates=[
            ValidationGateResult(
                gate_id="deterministic_tests",
                status="passed",
                evidence_path="adapter_compatibility_report.json",
            ),
            ValidationGateResult(
                gate_id="phoenix_trace",
                status="not_run",
                reason="no_live_mode",
            ),
        ],
        artifacts=[
            EvidenceArtifactRef(
                artifact_id="support_bundle.json",
                artifact_type="support_bundle",
                path="support_bundle.json",
                gate_id="support_bundle_artifact",
            )
        ],
    )

    report = build_harness_adapter_compatibility_report(
        adapter_id="chat_demo",
        events=_stream_events(),
        evidence_index=evidence_index,
        session_id="session_1",
        no_live=True,
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )

    assert report.feature_statuses["streaming"] == "supported"
    assert report.feature_statuses["cursor_reconnect"] == "supported"
    assert report.feature_statuses["approvals"] == "supported"
    assert report.feature_statuses["live_gates"] == "no_claim"
    assert report.validation_gate_statuses["phoenix_trace"] == "no_claim"
    assert report.support_bundle_refs[0].bundle_type == "support_bundle"
    assert report.run is not None
    assert report.run.current_cursor == "run_adapter:5"


def test_adapter_compatibility_artifacts_are_audit_visible(tmp_path) -> None:
    evidence_index = EvidenceArtifactIndex(
        index_id="adapter-excel",
        pack_id="excel_workbook_chat",
        scenario_ids=["harness_adapter.excel_workbook_chat.v1"],
        gates=[
            ValidationGateResult(
                gate_id="deterministic_tests",
                status="passed",
                evidence_path="adapter_compatibility_report.json",
            ),
            ValidationGateResult(
                gate_id="support_bundle_artifact",
                status="passed",
                evidence_path="adapter_compatibility_report.md",
            ),
            ValidationGateResult(
                gate_id="openrouter_live_preflight",
                status="no_claim",
                reason="no_live_mode",
            ),
        ],
        artifacts=[
            EvidenceArtifactRef(
                artifact_id="adapter_compatibility_report.json",
                artifact_type="adapter_compatibility_report",
                path="adapter_compatibility_report.json",
                gate_id="deterministic_tests",
            ),
        ],
    )
    report = build_harness_adapter_compatibility_report(
        adapter_id="excel_ai",
        events=_stream_events(),
        evidence_index=evidence_index,
        session_id="session_1",
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    rows = project_harness_adapter_events(_stream_events(), session_id="session_1")
    write_harness_adapter_compatibility_artifacts(tmp_path, report, events=rows)
    payload = json.loads(
        (tmp_path / "adapter_compatibility_report.json").read_text(encoding="utf-8")
    )
    assert payload["feature_statuses"]["live_gates"] == "no_claim"

    write_validation_artifacts(
        tmp_path,
        evidence_index=evidence_index.model_dump(mode="json"),
    )
    audit = audit_validation_evidence([tmp_path], strict=True, no_live=True)

    artifact_paths = audit["dashboard_summary"]["artifact_paths"]
    assert any("adapter_compatibility_report.json" in path for path in artifact_paths)
    assert audit["validation_run"]["scenario_ids"] == [
        "harness_adapter.excel_workbook_chat.v1"
    ]


def test_seed_scenarios_include_adapter_compatibility_pack_targets() -> None:
    scenarios = seed_scenario_specs()

    assert "harness_adapter.acp.basic_stream.v1" in scenarios
    assert "harness_adapter.a2a.basic_task.v1" in scenarios
    assert "harness_adapter.openai_server.basic_run.v1" in scenarios
    assert "harness_adapter.chat_demo.deep_research.v1" in scenarios
    assert "harness_adapter.excel_workbook_chat.v1" in scenarios
    assert "lifecycle_hooks.tool_transform_audit.v1" in scenarios
    assert "lifecycle_hooks.approval_interrupt_audit.v1" in scenarios
    assert "lifecycle_hooks.excel_workbook_policy.v1" in scenarios
    assert "lifecycle_hooks.chat_demo_research_policy.v1" in scenarios
    assert "durable_lifecycle.session_run_records.v1" in scenarios
    assert "durable_lifecycle.interrupt_resume_plan.v1" in scenarios
    assert "durable_lifecycle.background_attach_replay.v1" in scenarios
    assert "durable_lifecycle.excel_workbook_pause.v1" in scenarios
    assert "durable_lifecycle.chat_demo_research_pause.v1" in scenarios


def test_seed_harness_adapter_compatibility_reports_cover_three_targets() -> None:
    reports = seed_harness_adapter_compatibility_reports(
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc)
    )

    assert set(reports) == {"acp", "chat_demo", "excel_ai"}
    chat = reports["chat_demo"]
    assert chat.scenario_ids == ["harness_adapter.chat_demo.deep_research.v1"]
    assert chat.feature_statuses["artifacts"] == "supported"
    assert chat.feature_statuses["support_bundles"] == "supported"
    assert chat.feature_statuses["live_gates"] == "no_claim"
    assert any(ref.artifact_type == "report" for ref in chat.artifact_refs)
    excel = reports["excel_ai"]
    assert excel.scenario_ids == ["harness_adapter.excel_workbook_chat.v1"]
    assert excel.feature_statuses["approvals"] == "supported"
    assert excel.run is not None
    assert excel.run.lifecycle_state == "paused"
    assert any(ref.artifact_type == "workbook_context" for ref in excel.artifact_refs)
    acp = reports["acp"]
    assert acp.product_family == "generic_protocol"
    assert acp.feature_statuses["cursor_reconnect"] == "supported"
