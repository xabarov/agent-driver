"""Lifecycle hook compatibility fixtures and projections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent_driver.contracts.harness_adapter import HarnessAdapterEvent
from agent_driver.contracts.lifecycle_hooks import (
    LifecycleHookAuditRecord,
    LifecycleHookAuditStatus,
    LifecycleHookCompatibilityReport,
    LifecycleHookEvent,
    LifecycleHookEventType,
    LifecycleHookMode,
    LifecycleHookRegistration,
    LifecycleHookResult,
    LifecycleHookVerdict,
)

_PRODUCT_EVENTS = {
    "excel_ai": {
        LifecycleHookEventType.RUN_START,
        LifecycleHookEventType.PRE_TOOL_USE,
        LifecycleHookEventType.APPROVAL_REQUESTED,
        LifecycleHookEventType.ARTIFACT_CREATED,
        LifecycleHookEventType.RUN_FINALIZE,
    },
    "chat_demo": {
        LifecycleHookEventType.RUN_START,
        LifecycleHookEventType.TOOL_EVIDENCE_READY,
        LifecycleHookEventType.FILE_CHANGED,
        LifecycleHookEventType.INTERRUPT_REQUESTED,
        LifecycleHookEventType.RUN_FINALIZE,
    },
}


def project_lifecycle_hook_audit_events(
    records: list[LifecycleHookAuditRecord],
    *,
    session_id: str | None = None,
    source: str = "synthetic",
) -> list[HarnessAdapterEvent]:
    """Project lifecycle hook audit rows into compact adapter events."""
    projected: list[HarnessAdapterEvent] = []
    for index, record in enumerate(records, start=1):
        event = record.event
        result = record.result
        run_id = event.run_id or "unknown"
        seq = event.seq if event.seq > 0 else index
        projected.append(
            HarnessAdapterEvent(
                event_id=record.audit_id,
                session_id=session_id or event.session_id,
                run_id=run_id,
                attempt_id=event.attempt_id or "attempt_1",
                cursor=f"{run_id}:{seq}",
                seq=seq,
                kind=_runtime_kind_for_status(record.status),
                category="lifecycle_hook",
                state=record.status.value,
                source=source,
                display={
                    "title": f"{result.hook_id} {event.event_type.value}",
                    "summary": event.subject_summary
                    or f"lifecycle hook {result.verdict.value}",
                    "item_id": result.hook_id,
                    "category": "lifecycle_hook",
                    "state": record.status.value,
                },
                redacted_metadata={
                    "event_type": event.event_type.value,
                    "verdict": result.verdict.value,
                    "mode": result.continuation_behavior,
                    "timed_out": result.timed_out,
                    "error_class": result.error_class,
                    "artifact_refs": record.artifact_refs,
                },
                artifact_refs=[],
                support_bundle_refs=[],
            )
        )
    return projected


def build_lifecycle_hook_compatibility_report(
    *,
    product_family: str,
    records: list[LifecycleHookAuditRecord],
    registrations: list[LifecycleHookRegistration],
    generated_at: datetime | None = None,
    adapter_event_refs: list[str] | None = None,
    artifact_refs: list[str] | None = None,
) -> LifecycleHookCompatibilityReport:
    """Build a deterministic lifecycle hook compatibility report."""
    generated_at = generated_at or datetime.now(tz=UTC)
    supported = _supported_events(product_family, records)
    no_claim_events = {
        event.value: "host_protocol_does_not_claim_event"
        for event in LifecycleHookEventType
        if supported[event.value] == "no_claim"
    }
    return LifecycleHookCompatibilityReport(
        report_id=(
            f"{product_family}:lifecycle-hooks:"
            f"{generated_at.strftime('%Y%m%d%H%M%S')}"
        ),
        generated_at=generated_at.isoformat().replace("+00:00", "Z"),
        product_family=product_family,
        supported_events=supported,
        registrations=sorted(registrations, key=lambda item: item.order),
        modes_active={item.hook_id: item.mode.value for item in registrations},
        audit_record_count=len(records),
        adapter_event_refs=adapter_event_refs or [],
        artifact_refs=artifact_refs or [],
        missing_evidence=_missing_evidence(records, registrations),
        no_claim_events=no_claim_events,
        redacted_metadata={
            "projection": "deterministic",
            "live_evidence_claimed": False,
        },
    )


def seed_lifecycle_hook_compatibility_reports(
    *,
    generated_at: datetime | None = None,
) -> dict[str, LifecycleHookCompatibilityReport]:
    """Return deterministic no-live reports for Excel AI and chat-demo."""
    return {
        product: build_lifecycle_hook_compatibility_report(
            product_family=product,
            records=seed_lifecycle_hook_audit_records(product),
            registrations=seed_lifecycle_hook_registrations(product),
            generated_at=generated_at,
            adapter_event_refs=[f"{product}:lifecycle-hook-adapter-events.jsonl"],
            artifact_refs=[f"{product}:lifecycle-hook-compatibility-report.json"],
        )
        for product in ("excel_ai", "chat_demo")
    }


def seed_lifecycle_hook_registrations(
    product_family: str,
) -> list[LifecycleHookRegistration]:
    """Return metadata-only product hook registrations."""
    if product_family == "excel_ai":
        return [
            LifecycleHookRegistration(
                hook_id="excel.workbook_context.observe",
                owner="excel-ai-adapter",
                event_subscriptions=[LifecycleHookEventType.PRE_TOOL_USE],
                order=10,
                mode=LifecycleHookMode.OBSERVE,
                required_artifacts=["workbook_context"],
                compatibility_metadata={"declared": True, "product": "excel_ai"},
            ),
            LifecycleHookRegistration(
                hook_id="excel.edit_transaction.approval",
                owner="excel-ai-adapter",
                event_subscriptions=[LifecycleHookEventType.APPROVAL_REQUESTED],
                order=20,
                mode=LifecycleHookMode.OBSERVE,
                side_effect_permissions=["transactional_edit"],
                compatibility_metadata={"declared": True, "product": "excel_ai"},
            ),
        ]
    if product_family == "chat_demo":
        return [
            LifecycleHookRegistration(
                hook_id="chat_demo.source_evidence.observe",
                owner="chat-demo-adapter",
                event_subscriptions=[LifecycleHookEventType.TOOL_EVIDENCE_READY],
                order=10,
                mode=LifecycleHookMode.OBSERVE,
                required_artifacts=["sources"],
                compatibility_metadata={"declared": True, "product": "chat_demo"},
            ),
            LifecycleHookRegistration(
                hook_id="chat_demo.workspace_write.policy",
                owner="chat-demo-adapter",
                event_subscriptions=[LifecycleHookEventType.FILE_CHANGED],
                order=20,
                mode=LifecycleHookMode.OBSERVE,
                side_effect_permissions=["artifact_write"],
                compatibility_metadata={"declared": True, "product": "chat_demo"},
            ),
        ]
    return [
        LifecycleHookRegistration(
            hook_id="generic.lifecycle.observe",
            owner="agent-driver-harness",
            event_subscriptions=[LifecycleHookEventType.RUN_START],
            compatibility_metadata={"declared": True},
        )
    ]


def seed_lifecycle_hook_audit_records(
    product_family: str,
) -> list[LifecycleHookAuditRecord]:
    """Return deterministic product lifecycle hook audit fixtures."""
    run_id = f"{product_family}_lifecycle_run"
    if product_family == "excel_ai":
        rows = [
            _record(
                run_id=run_id,
                seq=1,
                hook_id="excel.workbook_context.observe",
                event_type=LifecycleHookEventType.PRE_TOOL_USE,
                verdict=LifecycleHookVerdict.OBSERVE,
                summary="workbook context requirement observed",
                artifact_refs=["workbook_context.json"],
            ),
            _record(
                run_id=run_id,
                seq=2,
                hook_id="excel.edit_transaction.approval",
                event_type=LifecycleHookEventType.APPROVAL_REQUESTED,
                verdict=LifecycleHookVerdict.REQUEST_APPROVAL,
                summary="transactional workbook edit approval requested",
                artifact_refs=["edit_transaction.json"],
            ),
            _record(
                run_id=run_id,
                seq=3,
                hook_id="excel.chart_artifact.observe",
                event_type=LifecycleHookEventType.ARTIFACT_CREATED,
                verdict=LifecycleHookVerdict.OBSERVE,
                summary="chart report artifact linked",
                artifact_refs=["chart_report.json"],
            ),
        ]
        return rows
    if product_family == "chat_demo":
        rows = [
            _record(
                run_id=run_id,
                seq=1,
                hook_id="chat_demo.source_evidence.observe",
                event_type=LifecycleHookEventType.TOOL_EVIDENCE_READY,
                verdict=LifecycleHookVerdict.OBSERVE,
                summary="source evidence requirement observed",
                artifact_refs=["sources.jsonl"],
            ),
            _record(
                run_id=run_id,
                seq=2,
                hook_id="chat_demo.workspace_write.policy",
                event_type=LifecycleHookEventType.FILE_CHANGED,
                verdict=LifecycleHookVerdict.REQUEST_APPROVAL,
                summary="workspace artifact write policy recorded",
                artifact_refs=["research/report.md"],
            ),
            _record(
                run_id=run_id,
                seq=3,
                hook_id="chat_demo.steering.interrupt",
                event_type=LifecycleHookEventType.INTERRUPT_REQUESTED,
                verdict=LifecycleHookVerdict.OBSERVE,
                summary="steering interrupt recorded for replay",
            ),
        ]
        return rows
    return [
        _record(
            run_id=run_id,
            seq=1,
            hook_id="generic.lifecycle.observe",
            event_type=LifecycleHookEventType.RUN_START,
            verdict=LifecycleHookVerdict.OBSERVE,
            summary="run start observed",
        )
    ]


def _supported_events(
    product_family: str, records: list[LifecycleHookAuditRecord]
) -> dict[str, str]:
    claimed = _PRODUCT_EVENTS.get(product_family, {LifecycleHookEventType.RUN_START})
    observed = {record.event.event_type for record in records}
    statuses: dict[str, str] = {}
    for event in LifecycleHookEventType:
        if event in observed or event in claimed:
            statuses[event.value] = "supported"
        else:
            statuses[event.value] = "no_claim"
    return statuses


def _missing_evidence(
    records: list[LifecycleHookAuditRecord],
    registrations: list[LifecycleHookRegistration],
) -> list[str]:
    available = {ref for record in records for ref in record.artifact_refs}
    missing: list[str] = []
    for registration in registrations:
        for artifact in registration.required_artifacts:
            if not any(artifact in ref for ref in available):
                missing.append(f"{registration.hook_id}:{artifact}")
    return sorted(missing)


def _record(
    *,
    run_id: str,
    seq: int,
    hook_id: str,
    event_type: LifecycleHookEventType,
    verdict: LifecycleHookVerdict,
    summary: str,
    artifact_refs: list[str] | None = None,
) -> LifecycleHookAuditRecord:
    event = LifecycleHookEvent(
        event_id=f"{run_id}:{seq}:{event_type.value}",
        event_type=event_type,
        run_id=run_id,
        attempt_id="attempt_1",
        session_id=f"{run_id}:session",
        seq=seq,
        source_component="fixture",
        subject_summary=summary,
        artifact_refs=artifact_refs or [],
    )
    result = LifecycleHookResult(
        hook_id=hook_id,
        verdict=verdict,
        elapsed_ms=0.0,
        action_metadata=_action_metadata(verdict),
    )
    return LifecycleHookAuditRecord(
        audit_id=f"{event.event_id}:{hook_id}",
        event=event,
        result=result,
        status=LifecycleHookAuditStatus.COMPLETED,
        artifact_refs=artifact_refs or [],
        created_at="2026-07-03T00:00:00Z",
    )


def _action_metadata(verdict: LifecycleHookVerdict) -> dict[str, Any]:
    if verdict == LifecycleHookVerdict.REQUEST_APPROVAL:
        return {"approval_requested": True}
    return {}


def _runtime_kind_for_status(status: LifecycleHookAuditStatus) -> str:
    return {
        LifecycleHookAuditStatus.STARTED: "lifecycle_hook_started",
        LifecycleHookAuditStatus.COMPLETED: "lifecycle_hook_completed",
        LifecycleHookAuditStatus.BLOCKED: "lifecycle_hook_blocked",
        LifecycleHookAuditStatus.FAILED: "lifecycle_hook_failed",
        LifecycleHookAuditStatus.TIMED_OUT: "lifecycle_hook_timed_out",
        LifecycleHookAuditStatus.SKIPPED: "lifecycle_hook_completed",
        LifecycleHookAuditStatus.NO_CLAIM: "lifecycle_hook_completed",
    }[status]


__all__ = [
    "build_lifecycle_hook_compatibility_report",
    "project_lifecycle_hook_audit_events",
    "seed_lifecycle_hook_audit_records",
    "seed_lifecycle_hook_compatibility_reports",
    "seed_lifecycle_hook_registrations",
]
