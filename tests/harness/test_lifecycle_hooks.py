"""Lifecycle hook compatibility harness tests."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_driver.contracts.capability_packs import (
    EvidenceArtifactIndex,
    EvidenceArtifactRef,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.harness import (
    build_lifecycle_hook_compatibility_report,
    project_lifecycle_hook_audit_events,
    seed_lifecycle_hook_audit_records,
    seed_lifecycle_hook_compatibility_reports,
    seed_lifecycle_hook_registrations,
)


def test_project_lifecycle_hook_audit_events_are_adapter_safe() -> None:
    records = seed_lifecycle_hook_audit_records("excel_ai")
    rows = project_lifecycle_hook_audit_events(records, session_id="session_1")

    assert [row.cursor for row in rows] == [
        "excel_ai_lifecycle_run:1",
        "excel_ai_lifecycle_run:2",
        "excel_ai_lifecycle_run:3",
    ]
    assert rows[0].category == "lifecycle_hook"
    assert rows[1].redacted_metadata["verdict"] == "request_approval"
    assert rows[1].source == "synthetic"


def test_lifecycle_hook_compatibility_report_marks_no_claim_events() -> None:
    records = seed_lifecycle_hook_audit_records("chat_demo")
    registrations = seed_lifecycle_hook_registrations("chat_demo")
    rows = project_lifecycle_hook_audit_events(records)

    report = build_lifecycle_hook_compatibility_report(
        product_family="chat_demo",
        records=records,
        registrations=registrations,
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        adapter_event_refs=[row.event_id for row in rows],
        artifact_refs=["lifecycle_hook_compatibility_report.json"],
    )

    assert report.report_id == "chat_demo:lifecycle-hooks:20260703000000"
    assert report.supported_events["tool_evidence_ready"] == "supported"
    assert report.supported_events["approval_resolved"] == "no_claim"
    assert report.no_claim_events["approval_resolved"] == (
        "host_protocol_does_not_claim_event"
    )
    assert report.audit_record_count == 3
    assert report.missing_evidence == []


def test_seed_lifecycle_hook_reports_cover_two_products() -> None:
    reports = seed_lifecycle_hook_compatibility_reports(
        generated_at=datetime(2026, 7, 3, tzinfo=timezone.utc)
    )

    assert set(reports) == {"excel_ai", "chat_demo"}
    assert reports["excel_ai"].modes_active["excel.edit_transaction.approval"] == (
        "observe"
    )
    assert reports["chat_demo"].supported_events["file_changed"] == "supported"
    assert reports["chat_demo"].redacted_metadata["live_evidence_claimed"] is False


def test_lifecycle_hook_artifact_types_are_evidence_index_visible() -> None:
    index = EvidenceArtifactIndex(
        index_id="lifecycle-hooks",
        pack_id="deep_research_chat_demo",
        scenario_ids=["lifecycle_hooks.chat_demo_research_policy.v1"],
        gates=[
            ValidationGateResult(
                gate_id="deterministic_tests",
                status="passed",
                evidence_path="lifecycle_hook_compatibility_report.json",
            )
        ],
        artifacts=[
            EvidenceArtifactRef(
                artifact_id="lifecycle_hook_compatibility_report.json",
                artifact_type="lifecycle_hook_compatibility_report",
                path="lifecycle_hook_compatibility_report.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="lifecycle_hook_audit.jsonl",
                artifact_type="lifecycle_hook_audit",
                path="lifecycle_hook_audit.jsonl",
                gate_id="deterministic_tests",
            ),
        ],
    )

    assert [item.artifact_type for item in index.artifacts] == [
        "lifecycle_hook_compatibility_report",
        "lifecycle_hook_audit",
    ]
