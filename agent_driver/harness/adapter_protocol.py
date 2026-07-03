"""Pure harness-adapter projection and compatibility report helpers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_driver.contracts import RuntimeEventType, new_runtime_event
from agent_driver.contracts.capability_packs import EvidenceArtifactIndex
from agent_driver.contracts.capability_packs import EvidenceArtifactRef as EvidenceRef
from agent_driver.contracts.harness_adapter import (
    HarnessAdapterCapability,
    HarnessAdapterCompatibilityReport,
    HarnessAdapterEvent,
    HarnessAdapterRun,
    HarnessAdapterSession,
    HarnessApprovalRequest,
    HarnessArtifactRef,
    HarnessSupportBundleRef,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.stream import (
    project_runtime_events,
    project_run_timeline,
    summarize_run_lifecycle,
    summarize_runtime_session_diagnostics,
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)
_SUPPORT_ARTIFACT_TYPES = {
    "support_bundle",
    "trace_summary",
    "phoenix_trace",
    "playwright_screenshot",
    "validation_gates",
    "validation_report_markdown",
    "validation_run_json",
}
_ADAPTER_SCENARIOS = {
    "acp": ["harness_adapter.acp.basic_stream.v1"],
    "chat_demo": ["harness_adapter.chat_demo.deep_research.v1"],
    "excel_ai": ["harness_adapter.excel_workbook_chat.v1"],
}
_PRODUCT_FAMILIES = {
    "acp": "generic_protocol",
    "chat_demo": "chat_demo",
    "excel_ai": "excel_ai",
}
_PACK_IDS = {
    "chat_demo": "deep_research_chat_demo",
    "excel_ai": "excel_workbook_chat",
}


def project_harness_adapter_events(
    events: Iterable[RunStreamEvent],
    *,
    session_id: str | None = None,
    source: str = "replay",
) -> list[HarnessAdapterEvent]:
    """Project stream events into adapter-safe rows with stable cursors."""
    ordered = sorted(events, key=lambda item: item.seq)
    by_seq = {event.seq: event for event in ordered}
    rows = project_run_timeline(ordered)
    projected: list[HarnessAdapterEvent] = []
    for row in rows:
        source_event = by_seq.get(row.seq)
        artifact_refs = (
            _artifact_refs_from_stream_event(source_event) if source_event else []
        )
        support_refs = _support_refs_from_artifacts(artifact_refs)
        approval = (
            _approval_request_from_stream_event(source_event)
            if source_event is not None
            else None
        )
        projected.append(
            HarnessAdapterEvent(
                event_id=row.row_id,
                session_id=session_id,
                run_id=row.run_id,
                attempt_id=row.attempt_id,
                cursor=f"{row.run_id}:{row.seq}",
                seq=row.seq,
                kind=source_event.event if source_event else row.category,
                category=row.category,
                state=row.state,
                source=source,
                display=_compact_display(row),
                redacted_metadata=_redact_mapping(
                    {
                        "diagnostics": row.diagnostics,
                        "app_metadata": row.app_metadata,
                    }
                ),
                artifact_refs=artifact_refs,
                support_bundle_refs=support_refs,
                approval_request=approval,
                created_at=row.created_at,
            )
        )
    return projected


def project_harness_adapter_artifacts(
    evidence_index: EvidenceArtifactIndex | None,
) -> list[HarnessArtifactRef]:
    """Project evidence-index artifacts into adapter-safe artifact refs."""
    if evidence_index is None:
        return []
    scenario_id = (
        evidence_index.scenario_ids[0] if evidence_index.scenario_ids else None
    )
    refs = [
        HarnessArtifactRef(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            uri=artifact.uri,
            sha256=artifact.sha256,
            gate_id=artifact.gate_id,
            scenario_id=scenario_id,
            redacted_metadata=_redact_mapping(artifact.redacted_metadata),
        )
        for artifact in evidence_index.artifacts
    ]
    return refs


def project_harness_support_bundle_refs(
    artifacts: Iterable[HarnessArtifactRef],
) -> list[HarnessSupportBundleRef]:
    """Return support/trace/validation refs from adapter artifact refs."""
    return _support_refs_from_artifacts(list(artifacts))


def project_harness_adapter_session(
    *,
    session_id: str,
    adapter_id: str,
    thread_id: str | None = None,
    cwd: str | None = None,
    durability_level: str = "unknown",
    lifecycle_state: str = "unknown",
    provider_route_summary: dict[str, Any] | None = None,
    support_bundle_refs: list[HarnessSupportBundleRef] | None = None,
    metadata: dict[str, Any] | None = None,
) -> HarnessAdapterSession:
    """Build a redaction-safe adapter session descriptor."""
    return HarnessAdapterSession(
        session_id=session_id,
        thread_id=thread_id,
        adapter_id=adapter_id,
        cwd=cwd,
        provider_route_summary=_redact_mapping(provider_route_summary or {}),
        lifecycle_state=lifecycle_state,
        durability_level=durability_level,
        support_bundle_refs=support_bundle_refs or [],
        redacted_metadata=_redact_mapping(metadata or {}),
    )


def project_harness_adapter_run(
    events: Iterable[RunStreamEvent],
    *,
    session_id: str | None = None,
    durability_level: str = "unknown",
    artifact_refs: list[HarnessArtifactRef] | None = None,
    support_bundle_refs: list[HarnessSupportBundleRef] | None = None,
    capability_pack_ids: list[str] | None = None,
    scenario_ids: list[str] | None = None,
    compatibility_flags: dict[str, str] | None = None,
) -> HarnessAdapterRun:
    """Build a run descriptor from stream events without starting a live run."""
    ordered = sorted(events, key=lambda item: item.seq)
    diagnostics = summarize_runtime_session_diagnostics(
        ordered, durability=durability_level, session_id=session_id
    )
    lifecycle = summarize_run_lifecycle(
        ordered, durability=durability_level, session_id=session_id
    )
    return HarnessAdapterRun(
        run_id=diagnostics.run_id or "unknown",
        attempt_id=diagnostics.attempt_id or "unknown",
        session_id=session_id or diagnostics.session_id,
        current_cursor=diagnostics.reconnect_cursor,
        lifecycle_state=str(lifecycle.state),
        durability_level=durability_level,
        supervisor_summary=_redact_mapping(
            {
                "terminal_event": lifecycle.terminal_event,
                "terminal_reason": lifecycle.terminal_reason,
                "last_event": lifecycle.last_event,
                "support_bundle_available": lifecycle.support_bundle_available,
            }
        ),
        capability_pack_ids=capability_pack_ids or [],
        scenario_ids=scenario_ids or [],
        artifact_refs=artifact_refs or [],
        support_bundle_refs=support_bundle_refs or [],
        compatibility_flags=compatibility_flags or {},
    )


def build_harness_adapter_capability(
    *,
    adapter_id: str,
    product_family: str | None = None,
    durability_level: str = "unknown",
    features: dict[str, str] | None = None,
    scenario_ids: list[str] | None = None,
    protocol: str = "harness_adapter",
) -> HarnessAdapterCapability:
    """Build a feature manifest with truthful default no-claim statuses."""
    default_features = {
        "streaming": "supported",
        "replay": "supported",
        "cursor_reconnect": "supported",
        "approvals": "no_claim",
        "interrupts": "supported",
        "artifacts": "no_claim",
        "support_bundles": "no_claim",
        "fork": "unsupported",
        "background_logs": "unsupported",
        "ui_projection": "no_claim",
        "live_gates": "no_claim",
    }
    default_features.update(features or {})
    return HarnessAdapterCapability(
        adapter_id=adapter_id,
        product_family=product_family or _PRODUCT_FAMILIES.get(adapter_id, adapter_id),
        protocol=protocol,
        durability_level=durability_level,
        features=default_features,
        scenario_ids=scenario_ids or _ADAPTER_SCENARIOS.get(adapter_id, []),
        redacted_metadata={"projection": "metadata_only"},
    )


def build_harness_adapter_compatibility_report(
    *,
    adapter_id: str,
    events: Iterable[RunStreamEvent] = (),
    evidence_index: EvidenceArtifactIndex | None = None,
    session_id: str | None = None,
    product_family: str | None = None,
    protocol: str = "harness_adapter",
    durability_level: str = "process_local",
    no_live: bool = True,
    generated_at: datetime | None = None,
    feature_overrides: dict[str, str] | None = None,
) -> HarnessAdapterCompatibilityReport:
    """Build a deterministic adapter compatibility report."""
    generated_at = generated_at or datetime.now(timezone.utc)
    ordered = sorted(events, key=lambda item: item.seq)
    projected_events = project_harness_adapter_events(
        ordered, session_id=session_id, source="replay"
    )
    artifacts = project_harness_adapter_artifacts(evidence_index)
    stream_artifacts = [
        ref for event in projected_events for ref in event.artifact_refs
    ]
    all_artifacts = artifacts + [
        ref
        for ref in stream_artifacts
        if ref.artifact_id not in {a.artifact_id for a in artifacts}
    ]
    support_refs = project_harness_support_bundle_refs(all_artifacts)
    scenario_ids = (
        list(evidence_index.scenario_ids)
        if evidence_index is not None and evidence_index.scenario_ids
        else _ADAPTER_SCENARIOS.get(adapter_id, [])
    )
    feature_statuses = _feature_statuses(
        projected_events=projected_events,
        artifacts=all_artifacts,
        support_refs=support_refs,
        no_live=no_live,
    )
    feature_statuses.update(feature_overrides or {})
    capability = build_harness_adapter_capability(
        adapter_id=adapter_id,
        product_family=product_family,
        durability_level=durability_level,
        features=feature_statuses,
        scenario_ids=scenario_ids,
        protocol=protocol,
    )
    run = (
        project_harness_adapter_run(
            ordered,
            session_id=session_id,
            durability_level=durability_level,
            artifact_refs=all_artifacts,
            support_bundle_refs=support_refs,
            scenario_ids=scenario_ids,
            compatibility_flags=feature_statuses,
        )
        if ordered
        else None
    )
    session = (
        project_harness_adapter_session(
            session_id=session_id,
            adapter_id=adapter_id,
            durability_level=durability_level,
            support_bundle_refs=support_refs,
            metadata={"event_count": len(projected_events)},
        )
        if session_id
        else None
    )
    validation_gate_statuses = (
        {
            gate.gate_id: _adapter_status_from_gate(gate.status)
            for gate in evidence_index.gates
        }
        if evidence_index is not None
        else {}
    )
    skipped_reasons = (
        {
            gate.gate_id: gate.reason
            for gate in evidence_index.gates
            if gate.status in {"skipped", "no_claim", "stale"} and gate.reason
        }
        if evidence_index is not None
        else {}
    )
    return HarnessAdapterCompatibilityReport(
        report_id=(f"{adapter_id}:{protocol}:{generated_at.strftime('%Y%m%d%H%M%S')}"),
        adapter_id=adapter_id,
        product_family=product_family or _PRODUCT_FAMILIES.get(adapter_id, adapter_id),
        protocol=protocol,
        generated_at=generated_at.isoformat(),
        no_live=no_live,
        capability=capability,
        feature_statuses=feature_statuses,
        session=session,
        run=run,
        event_count=len(projected_events),
        artifact_refs=all_artifacts,
        support_bundle_refs=support_refs,
        scenario_ids=scenario_ids,
        validation_gate_statuses=validation_gate_statuses,
        skipped_reasons=skipped_reasons,
        redacted_metadata={
            "projection": "deterministic",
            "live_evidence_claimed": not no_live,
        },
    )


def render_harness_adapter_compatibility_markdown(
    report: HarnessAdapterCompatibilityReport,
) -> str:
    """Render a compact owner-handoff compatibility report."""
    lines = [
        f"# Harness Adapter Compatibility {report.report_id}",
        "",
        f"Adapter: `{report.adapter_id}`",
        f"Product family: `{report.product_family}`",
        f"Protocol: `{report.protocol}`",
        f"No-live mode: `{str(report.no_live).lower()}`",
        "",
        "## Features",
        "",
        "| Feature | Status |",
        "|---|---|",
    ]
    for feature, status in sorted(report.feature_statuses.items()):
        lines.append(f"| {feature} | {status} |")
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Events: {report.event_count}",
            f"- Artifacts: {len(report.artifact_refs)}",
            f"- Support refs: {len(report.support_bundle_refs)}",
            f"- Scenarios: {', '.join(report.scenario_ids) or 'none'}",
            "",
            "## Gate Statuses",
            "",
            "| Gate | Status |",
            "|---|---|",
        ]
    )
    for gate_id, status in sorted(report.validation_gate_statuses.items()):
        lines.append(f"| {gate_id} | {status} |")
    if not report.validation_gate_statuses:
        lines.append("| none | no_claim |")
    lines.append("")
    return "\n".join(lines)


def write_harness_adapter_compatibility_artifacts(
    output_dir: str | Path,
    report: HarnessAdapterCompatibilityReport,
    *,
    events: Iterable[HarnessAdapterEvent] = (),
) -> dict[str, Any]:
    """Persist adapter compatibility JSON/Markdown and optional event rows."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "adapter_compatibility_report.json"
    md_path = root / "adapter_compatibility_report.md"
    events_path = root / "adapter_events.jsonl"
    payload = report.model_dump(mode="json")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(
        render_harness_adapter_compatibility_markdown(report),
        encoding="utf-8",
    )
    event_rows = [event.model_dump(mode="json") for event in events]
    if event_rows:
        events_path.write_text(
            "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in event_rows),
            encoding="utf-8",
        )
    artifacts = [
        _artifact_row(json_path, "adapter_compatibility_report", root=root),
        _artifact_row(md_path, "adapter_compatibility_report", root=root),
    ]
    if event_rows:
        artifacts.append(_artifact_row(events_path, "adapter_events", root=root))
    manifest = {
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "redaction": {"safe_by_default": True},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "adapter_compatibility_report_json": str(json_path),
        "adapter_compatibility_report_markdown": str(md_path),
        "adapter_events_jsonl": str(events_path) if event_rows else None,
        "manifest": str(manifest_path),
    }


def seed_harness_adapter_compatibility_reports(
    *,
    generated_at: datetime | None = None,
) -> dict[str, HarnessAdapterCompatibilityReport]:
    """Return deterministic no-live compatibility reports for seed adapters."""
    return {
        adapter_id: build_harness_adapter_compatibility_report(
            adapter_id=adapter_id,
            events=_seed_stream_events(adapter_id),
            evidence_index=_seed_evidence_index(adapter_id),
            session_id=f"{adapter_id}_session",
            no_live=True,
            generated_at=generated_at,
        )
        for adapter_id in ("acp", "chat_demo", "excel_ai")
    }


def _feature_statuses(
    *,
    projected_events: list[HarnessAdapterEvent],
    artifacts: list[HarnessArtifactRef],
    support_refs: list[HarnessSupportBundleRef],
    no_live: bool,
) -> dict[str, str]:
    statuses = {
        "streaming": "supported" if projected_events else "no_claim",
        "replay": "supported" if projected_events else "no_claim",
        "cursor_reconnect": (
            "supported"
            if projected_events
            and all(
                event.cursor == f"{event.run_id}:{event.seq}"
                for event in projected_events
            )
            else "no_claim"
        ),
        "approvals": (
            "supported"
            if any(event.approval_request is not None for event in projected_events)
            else "no_claim"
        ),
        "interrupts": (
            "supported"
            if any(event.category == "interrupt" for event in projected_events)
            else "no_claim"
        ),
        "artifacts": "supported" if artifacts else "no_claim",
        "support_bundles": "supported" if support_refs else "no_claim",
        "fork": "unsupported",
        "background_logs": "unsupported",
        "ui_projection": "no_claim",
        "live_gates": "no_claim" if no_live else "supported",
    }
    return statuses


def _seed_stream_events(adapter_id: str) -> list[RunStreamEvent]:
    run_id = f"{adapter_id}_adapter_run"
    session_id = f"{adapter_id}_session"
    common_start = new_runtime_event(
        event_type=RuntimeEventType.RUN_STARTED,
        context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 1},
        options={
            "payload": {
                "adapter_id": adapter_id,
                "session_id": session_id,
                "capability_scenario_ids": _ADAPTER_SCENARIOS.get(adapter_id, []),
            }
        },
    )
    if adapter_id == "chat_demo":
        events = [
            common_start,
            new_runtime_event(
                event_type=RuntimeEventType.CONTROL_REQUESTED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 2},
                options={
                    "payload": {
                        "control_id": "steer_1",
                        "kind": "steering_update",
                        "summary": "user steering queued",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.SOURCE_LEDGER_UPDATED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 3},
                options={"payload": {"record_count": 3, "summary": "sources indexed"}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.ARTIFACT_CREATED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 4},
                options={
                    "payload": {
                        "artifact_id": "research/report.md",
                        "artifact_type": "report",
                        "path": "research/report.md",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 5},
            ),
        ]
        return project_runtime_events(events)
    if adapter_id == "excel_ai":
        events = [
            common_start,
            new_runtime_event(
                event_type=RuntimeEventType.ARTIFACT_CREATED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 2},
                options={
                    "payload": {
                        "artifact_id": "workbook-context",
                        "artifact_type": "workbook_context",
                        "path": "evidence/workbook_context.json",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.INTERRUPT_REQUESTED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 3},
                options={
                    "payload": {
                        "interrupt_id": "approval_excel_edit",
                        "tool_name": "excel_apply_edit",
                        "tool_call_id": "excel_edit_1",
                        "side_effect_class": "transactional_edit",
                        "args_summary": "apply workbook transaction",
                        "allowed_actions": ["approve", "reject"],
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_PAUSED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 4},
                options={
                    "payload": {
                        "interrupt_id": "approval_excel_edit",
                        "summary": "awaiting workbook edit approval",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.ARTIFACT_CREATED,
                context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 5},
                options={
                    "payload": {
                        "artifact_id": "chart-report",
                        "artifact_type": "chart_report",
                        "path": "evidence/chart_report.json",
                    }
                },
            ),
        ]
        return project_runtime_events(events)
    events = [
        common_start,
        new_runtime_event(
            event_type=RuntimeEventType.TOOL_CALL_STARTED,
            context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 2},
            options={
                "payload": {
                    "tool_name": "read_file",
                    "tool_call_id": "read_1",
                    "args_summary": "read workspace file",
                }
            },
        ),
        new_runtime_event(
            event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
            context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 3},
            options={
                "payload": {
                    "tool_name": "read_file",
                    "tool_call_id": "read_1",
                    "status": "completed",
                    "result_summary": "read completed",
                }
            },
        ),
        new_runtime_event(
            event_type=RuntimeEventType.RUN_COMPLETED,
            context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 4},
        ),
    ]
    return project_runtime_events(events)


def _seed_evidence_index(adapter_id: str) -> EvidenceArtifactIndex:
    scenario_ids = _ADAPTER_SCENARIOS.get(adapter_id, [])
    return EvidenceArtifactIndex(
        index_id=f"{adapter_id}:adapter-compatibility:seed",
        pack_id=_PACK_IDS.get(adapter_id),
        scenario_ids=scenario_ids,
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
                reason="no_live_adapter_compatibility_seed",
            ),
            ValidationGateResult(
                gate_id="phoenix_trace",
                status="no_claim",
                reason="no_live_adapter_compatibility_seed",
            ),
            ValidationGateResult(
                gate_id="playwright_ui",
                status="no_claim",
                reason="ui_projection_not_changed",
            ),
        ],
        artifacts=[
            EvidenceRef(
                artifact_id="adapter_compatibility_report.json",
                artifact_type="adapter_compatibility_report",
                path="adapter_compatibility_report.json",
                gate_id="deterministic_tests",
            ),
            EvidenceRef(
                artifact_id="adapter_compatibility_report.md",
                artifact_type="adapter_compatibility_report",
                path="adapter_compatibility_report.md",
                gate_id="support_bundle_artifact",
            ),
            EvidenceRef(
                artifact_id="support_bundle.json",
                artifact_type="support_bundle",
                path="support_bundle.json",
                gate_id="support_bundle_artifact",
            ),
        ],
    )


def _adapter_status_from_gate(status: str) -> str:
    if status in {"passed", "skipped", "stale", "no_claim", "failed"}:
        return status
    if status in {"not_run", "quarantined"}:
        return "no_claim"
    if status == "blocked":
        return "failed"
    return "no_claim"


def _compact_display(row: Any) -> dict[str, Any]:
    return _redact_mapping(
        {
            "title": row.title,
            "summary": row.summary,
            "item_id": row.item_id,
            "parent_id": row.parent_id,
            "category": row.category,
            "state": row.state,
        }
    )


def _approval_request_from_stream_event(
    event: RunStreamEvent,
) -> HarnessApprovalRequest | None:
    if event.event not in {
        "interrupt_requested",
        "plan_approval_requested",
        "run_paused",
        "run_requires_action",
    }:
        return None
    data = event.data
    interrupt = data.get("interrupt")
    if isinstance(interrupt, dict):
        data = {**data, **interrupt}
    request_id = (
        _text(data.get("interrupt_id"))
        or _text(data.get("request_id"))
        or _text(data.get("approval_id"))
        or f"{event.run_id}:{event.seq}:approval"
    )
    return HarnessApprovalRequest(
        request_id=request_id,
        run_id=event.run_id,
        attempt_id=event.attempt_id,
        tool_name=_text(data.get("tool_name")),
        tool_call_id=_text(data.get("tool_call_id")),
        side_effect_class=_text(data.get("side_effect_class"))
        or _text(data.get("side_effect")),
        arguments_summary=_text(data.get("args_summary"))
        or _text(data.get("arguments_summary")),
        policy_verdict=_text(data.get("policy_verdict")) or _text(data.get("reason")),
        expires_at=_text(data.get("expires_at")),
        response_options=_string_list(data.get("allowed_actions"))
        or _string_list(data.get("response_options"))
        or ["approve", "reject"],
        redacted_details=_redact_mapping(
            {
                "title": data.get("title"),
                "description": data.get("description"),
                "risk": data.get("risk"),
            }
        ),
    )


def _artifact_refs_from_stream_event(
    event: RunStreamEvent,
) -> list[HarnessArtifactRef]:
    if event.event not in {"artifact_created", "artifact_updated"}:
        return []
    data = event.data
    artifact_id = (
        _text(data.get("artifact_id"))
        or _text(data.get("path"))
        or f"{event.run_id}:{event.seq}:artifact"
    )
    artifact_type = (
        _text(data.get("artifact_type")) or _text(data.get("kind")) or "file"
    )
    return [
        HarnessArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=_text(data.get("path")),
            uri=_text(data.get("uri")),
            sha256=_text(data.get("sha256")),
            gate_id=_text(data.get("gate_id")),
            scenario_id=_text(data.get("scenario_id")),
            redacted_metadata=_redact_mapping(
                {
                    key: value
                    for key, value in data.items()
                    if key
                    not in {
                        "artifact_id",
                        "artifact_type",
                        "kind",
                        "path",
                        "uri",
                        "sha256",
                    }
                }
            ),
        )
    ]


def _support_refs_from_artifacts(
    artifacts: Iterable[HarnessArtifactRef],
) -> list[HarnessSupportBundleRef]:
    refs: list[HarnessSupportBundleRef] = []
    for artifact in artifacts:
        if artifact.artifact_type not in _SUPPORT_ARTIFACT_TYPES:
            continue
        refs.append(
            HarnessSupportBundleRef(
                bundle_id=artifact.artifact_id,
                bundle_type=artifact.artifact_type,
                path=artifact.path,
                uri=artifact.uri,
                sha256=artifact.sha256,
                gate_id=artifact.gate_id,
                redacted_metadata=artifact.redacted_metadata,
            )
        )
    return refs


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _redact_value(key, item) for key, item in value.items()}


def _redact_value(key: object, value: Any) -> Any:
    key_text = str(key).lower()
    if any(marker in key_text for marker in _SECRET_KEY_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, str):
        text = _SECRET_VALUE_RE.sub("<redacted>", value)
        if any(
            marker in text.lower()
            for marker in ("?token=", "api_key=", "access_token=")
        ):
            return "<redacted>"
        return text[:500]
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _artifact_row(path: Path, artifact_type: str, *, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact_type": artifact_type,
        "path": str(path.relative_to(root)),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


__all__ = [
    "build_harness_adapter_capability",
    "build_harness_adapter_compatibility_report",
    "project_harness_adapter_artifacts",
    "project_harness_adapter_events",
    "project_harness_adapter_run",
    "project_harness_adapter_session",
    "project_harness_support_bundle_refs",
    "render_harness_adapter_compatibility_markdown",
    "seed_harness_adapter_compatibility_reports",
    "write_harness_adapter_compatibility_artifacts",
]
