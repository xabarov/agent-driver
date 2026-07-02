"""Tests for trace-safe provenance summaries."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.contracts.context import (
    ArtifactProvenance,
    ContextProvenanceRecord,
    SideEffectTransaction,
    SkillAttachment,
    SourceEvidenceRecord,
)
from agent_driver.observability import (
    build_persisted_support_bundle,
    build_provenance_summary,
    build_runtime_support_bundle,
    summarize_run_trace,
)


def test_provenance_contracts_are_json_safe() -> None:
    context = ContextProvenanceRecord(
        context_id="ctx_workbook_overview",
        kind="workbook_overview",
        source_ref="workbook://overview",
        status="compacted",
        token_estimate=120,
        product_tags=["excel_ai"],
    )
    skill = SkillAttachment(
        skill_id="spreadsheet-chart",
        name="spreadsheet-chart",
        version="1.0.0",
        redacted_manifest_checksum="abc123...",
    )
    artifact = ArtifactProvenance(
        artifact_id="research/report.md",
        artifact_type="report",
        path="research/report.md",
        safe_path_classification="workspace_research",
    )
    source = SourceEvidenceRecord(
        source_id="source:1",
        source_type="web_fetch",
        canonical_url="https://example.com/",
        domain="example.com",
    )
    transaction = SideEffectTransaction(
        transaction_id="tx_1",
        side_effect_class="workbook-edit",
        apply_status="applied",
        rollback_status="available",
    )

    assert context.model_dump(mode="json")["status"] == "compacted"
    assert skill.model_dump(mode="json")["redacted_manifest_checksum"] == "abc123..."
    assert artifact.model_dump(mode="json")["path"] == "research/report.md"
    assert source.model_dump(mode="json")["domain"] == "example.com"
    assert transaction.model_dump(mode="json")["side_effect_class"] == "workbook-edit"


def test_build_provenance_summary_from_metadata_and_events() -> None:
    summary = build_provenance_summary(
        metadata={
            "context_provenance": [
                {
                    "context_id": "ctx_workbook_overview",
                    "kind": "workbook_overview",
                    "source_ref": "workbook://overview",
                    "status": "attached",
                    "api_key": "must-redact",
                    "content": "must-not-appear",
                }
            ],
            "invoked_skill_refs": [
                {
                    "name": "deep-research-report",
                    "path": "/repo/skills/deep-research-report/SKILL.md",
                    "digest": "1234567890abcdef",
                    "trusted": True,
                }
            ],
            "memory_fact_provenance": [
                {
                    "fact_id": "mem_1",
                    "source_ref": "workbook://sheet/Sheet1",
                    "link_check_status": "ok",
                    "compaction_survival_status": "retained",
                }
            ],
        },
        events=[
            {
                "event": "artifact_created",
                "run_id": "run_1",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "tool_name": "file_write",
                    "tool_call_id": "call_report",
                    "size_bytes": 123,
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "tool_call_id": "call_fetch",
                            "status": "completed",
                            "source_evidence": [
                                {
                                    "id": "web_fetch:call_fetch:1",
                                    "canonical_url": "https://example.com/",
                                    "domain": "example.com",
                                    "source_type": "web_fetch",
                                }
                            ],
                            "args": {"url": "https://example.com"},
                        }
                    ]
                },
            },
        ],
        required_evidence=[
            "context_provenance",
            "skill_attachments",
            "artifact_provenance",
            "source_evidence",
        ],
    )

    assert summary["context_provenance"]["counts_by_kind"] == {
        "workbook_overview": 1
    }
    assert "api_key" not in summary["context_provenance"]["records"][0]["metadata"]
    assert summary["skill_attachments"]["attachments"][0]["name"] == (
        "deep-research-report"
    )
    assert summary["skill_attachments"]["attachments"][0][
        "redacted_manifest_checksum"
    ] == "1234567890ab..."
    assert summary["memory_fact_provenance"]["link_check_statuses"] == ["ok"]
    assert summary["artifact_provenance"]["paths"] == ["research/report.md"]
    assert summary["source_evidence"]["domains"] == ["example.com"]
    assert "artifact-create" in summary["side_effect_transactions"]["classes"]
    assert summary["contract_verdicts"]["status"] == "pass"


def test_runtime_support_bundle_contains_redacted_provenance_sections() -> None:
    output = AgentRunOutput(
        run_id="run_prov",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.ARTIFACT_CREATED,
                context={"run_id": "run_prov", "attempt_id": "attempt_1", "seq": 1},
                options={
                    "payload": {
                        "path": "research/report.md",
                        "kind": "report",
                        "tool_name": "file_write",
                        "tool_call_id": "call_report",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": "run_prov", "attempt_id": "attempt_1", "seq": 2},
            ),
        ],
        metadata={
            "required_evidence": ["context_provenance", "artifact_provenance"],
            "context_provenance": [
                {
                    "context_id": "ctx_source_note",
                    "kind": "source_note",
                    "source_ref": "research/sources.jsonl#1",
                    "status": "compacted",
                    "redaction_level": "summary",
                    "raw_content": "do not leak",
                }
            ],
        },
    )

    bundle = build_runtime_support_bundle(output)

    assert bundle["context_provenance"]["compacted_count"] == 1
    assert bundle["context_provenance"]["records"][0]["source_ref"] == (
        "research/sources.jsonl#1"
    )
    assert "raw_content" not in bundle["context_provenance"]["records"][0]
    assert bundle["artifact_provenance"]["paths"] == ["research/report.md"]
    assert bundle["contract_verdicts"]["status"] == "pass"


def test_persisted_support_bundle_rebuilds_stable_provenance_from_replay() -> None:
    bundle = build_persisted_support_bundle(
        {
            "run_id": "run_replay",
            "event_count": 2,
            "events": [
                {
                    "type": "artifact_updated",
                    "seq": 1,
                    "payload": {
                        "path": "research/sources.jsonl",
                        "kind": "research",
                        "tool_name": "source_ledger",
                        "record_count": 2,
                    },
                },
                {"type": "run_completed", "seq": 2, "payload": {}},
            ],
            "metadata": {
                "source_evidence": [
                    {
                        "id": "web_fetch:call_1:1",
                        "canonical_url": "https://example.com/a",
                        "domain": "example.com",
                        "source_type": "web_fetch",
                    }
                ]
            },
        }
    )

    assert bundle["artifact_provenance"]["paths"] == ["research/sources.jsonl"]
    assert bundle["source_evidence"]["count"] == 1
    assert bundle["source_evidence"]["domains"] == ["example.com"]
    assert bundle["run_lifecycle"]["reconnect_cursor"] == "run_replay:2"


def test_run_trace_summary_flags_required_missing_provenance_and_survival() -> None:
    summary = summarize_run_trace(
        run_id="run_trace_prov",
        user_prompt="Summarize the source-backed report.",
        assistant_text="Done: research/report.md",
        task_contract={
            "required_evidence": [
                "context_provenance",
                "skill_attachments",
                "source_evidence",
            ]
        },
        events=[
            {
                "event": "run_started",
                "data": {
                    "context_provenance": [
                        {
                            "context_id": "ctx_source_note",
                            "kind": "source_note",
                            "source_ref": "research/sources.jsonl#1",
                            "status": "compacted",
                        },
                        {
                            "context_id": "ctx_full_page",
                            "kind": "source_page",
                            "status": "dropped",
                        },
                    ],
                    "skill_attachments": [
                        {
                            "skill_id": "deep-research-report",
                            "name": "deep-research-report",
                            "status": "attached",
                        }
                    ],
                    "required_evidence": [
                        "context_provenance",
                        "skill_attachments",
                        "source_evidence",
                    ],
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["context_provenance"]["compacted_count"] == 1
    assert summary["context_provenance"]["dropped_count"] == 1
    assert summary["skill_attachments"]["count"] == 1
    assert summary["provenance_contracts"]["violations"][
        "missing_source_evidence"
    ] is True
    assert summary["failures"]["missing_source_evidence"] is True
