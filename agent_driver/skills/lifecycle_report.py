"""Skill lifecycle reporting: compatibility report, usage summary, hook/adapter
projections, support-bundle projection, and deterministic seeds.

Split out of ``skills/lifecycle.py`` (god-module split, behaviour-neutral);
re-exported from ``lifecycle`` for existing callers.
"""


from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from agent_driver.contracts.context.provenance import SkillAttachment
from agent_driver.contracts.harness_adapter import (
    HarnessAdapterEvent,
    HarnessArtifactRef,
    HarnessSupportBundleRef,
)
from agent_driver.contracts.lifecycle_hooks import (
    LifecycleHookAuditRecord,
    LifecycleHookAuditStatus,
    LifecycleHookEvent,
    LifecycleHookEventType,
    LifecycleHookResult,
    LifecycleHookVerdict,
)
from agent_driver.contracts.skills_lifecycle import (
    SkillCapabilityFilter,
    SkillInventoryRecord,
    SkillInventorySnapshot,
    SkillInvocationRecord,
    SkillLifecycleCompatibilityReport,
    SkillLockFile,
    SkillSelectionDecision,
    SkillSelectionRequest,
    SkillSupportingFileRef,
    SkillUsageSummary,
)
from agent_driver.skills.curated import curated_skills_dir
from agent_driver.skills.registry import SkillView
from agent_driver.skills.lifecycle_common import (
    _primary_skill_scenario,
)
from agent_driver.skills.lifecycle_inventory import (
    _slug,
    _stable_digest,
    build_skill_inventory_snapshot,
    build_skill_lock_file,
    build_skill_selection_decisions,
    stable_skill_id,
)


def support_bundle_projection(
    decisions: list[SkillSelectionDecision],
) -> list[dict[str, Any]]:
    """Project decisions into support-bundle-safe rows."""
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        rows.append(
            {
                "decision_id": decision.decision_id,
                "skill_id": decision.skill_id,
                "name": decision.name,
                "digest": decision.digest,
                "status": decision.status,
                "rationale": decision.rationale,
                "filter_reasons": decision.filter_reasons,
                "safety_warnings": decision.safety_warnings,
                "redaction_status": decision.redaction_status,
            }
        )
    return rows


def invocation_record_from_view(
    view: SkillView,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    artifact_refs: list[str] | None = None,
    provenance_refs: list[str] | None = None,
) -> SkillInvocationRecord:
    """Expand a SkillView invocation into lifecycle telemetry."""
    skill_id = stable_skill_id(view.manifest)
    supporting_file = None
    if view.relative_file:
        supporting_file = SkillSupportingFileRef(
            relative_path=view.relative_file,
            size_bytes=len(view.content.encode("utf-8")),
            checksum=sha256(view.content.encode("utf-8")).hexdigest(),
            kind="file",
            read_status="truncated" if view.truncated else "read",
            safety_scan_status="not_scanned" if view.manifest.trusted else "passed",
            source_skill_id=skill_id,
        )
    return SkillInvocationRecord(
        invocation_id=f"skill-invocation:{skill_id}:{view.invocation.tool_call_id or 'manual'}",
        skill_id=skill_id,
        name=view.manifest.name,
        digest=view.manifest.digest,
        content_kind=view.content_kind,
        supporting_file=supporting_file,
        truncated=view.truncated,
        safety_scan_status="not_scanned" if view.manifest.trusted else "passed",
        tool_call_id=view.invocation.tool_call_id,
        run_id=run_id,
        session_id=session_id,
        artifact_refs=list(artifact_refs or []),
        provenance_refs=list(provenance_refs or []),
    )


def skill_attachment_from_record(
    record: SkillInventoryRecord,
    *,
    activation_reason: str | None = None,
    status: str = "attached",
) -> SkillAttachment:
    """Project an inventory record into existing provenance attachment shape."""
    return SkillAttachment(
        skill_id=record.skill_id,
        name=record.name,
        version=record.version,
        source=record.source,
        activation_reason=activation_reason,
        status=status,
        resolved_path=record.resolved_path,
        package_source=record.source_ref,
        compatibility_flags=record.compatibility_flags,
        redacted_manifest_checksum=record.digest,
        metadata={
            "trusted": record.trusted,
            "supporting_file_refs": [
                item.relative_path for item in record.supporting_files
            ],
        },
    )


def build_skill_lifecycle_compatibility_report(
    *,
    report_id: str,
    product_family: str,
    host_profile: str,
    snapshot: SkillInventorySnapshot | None = None,
    lockfile: SkillLockFile | None = None,
    filters_applied: list[SkillCapabilityFilter] | None = None,
    selections_made: list[SkillSelectionDecision] | None = None,
    invocations_recorded: list[SkillInvocationRecord] | None = None,
    usage_summary: SkillUsageSummary | None = None,
    no_claims: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SkillLifecycleCompatibilityReport:
    """Build a compact host adoption report."""
    records = (
        lockfile.skill_refs
        if lockfile
        else (snapshot.manifest_refs if snapshot else [])
    )
    decisions = list(selections_made or [])
    invocations = list(invocations_recorded or [])
    attachments = [
        skill_attachment_from_record(record).model_dump(mode="json")
        for record in records
    ]
    return SkillLifecycleCompatibilityReport(
        report_id=report_id,
        product_family=product_family,
        host_profile=host_profile,
        inventory_snapshot_id=snapshot.snapshot_id if snapshot else None,
        lock_id=lockfile.lock_id if lockfile else None,
        roots_scanned=snapshot.root_refs if snapshot else [],
        locks_verified=[lockfile.lock_id] if lockfile else [],
        filters_applied=list(filters_applied or []),
        selections_made=decisions,
        invocations_recorded=invocations,
        usage_summary=usage_summary
        or build_skill_usage_summary(
            records=records,
            decisions=decisions,
            invocations=invocations,
        ),
        provenance_rows_emitted=attachments,
        no_claims=list(no_claims or []),
        support_bundle_projection=support_bundle_projection(decisions),
        evidence_refs=list(evidence_refs or []),
        metadata=dict(metadata or {}),
    )


def build_skill_usage_summary(
    *,
    records: list[SkillInventoryRecord],
    decisions: list[SkillSelectionDecision],
    invocations: list[SkillInvocationRecord],
    outcome_links: dict[str, Any] | None = None,
) -> SkillUsageSummary:
    """Summarize deterministic usage counters without quality claims."""
    return SkillUsageSummary(
        discovered=len(records),
        selected=sum(1 for item in decisions if item.status == "selected"),
        viewed=sum(1 for item in invocations if item.content_kind == "skill"),
        supporting_file_viewed=sum(
            1 for item in invocations if item.content_kind == "supporting_file"
        ),
        filtered=sum(1 for item in decisions if item.status == "filtered"),
        blocked=sum(1 for item in decisions if item.status == "blocked"),
        failed=sum(1 for item in decisions if item.status == "failed"),
        stale=sum(1 for item in records if item.status == "stale"),
        outcome_links=dict(outcome_links or {}),
    )


def project_skill_lifecycle_hook_audit_records(
    report: SkillLifecycleCompatibilityReport,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
) -> list[LifecycleHookAuditRecord]:
    """Project skill lifecycle evidence into lifecycle hook audit rows."""
    actual_run_id = run_id or f"{_slug(report.product_family)}_skills_lifecycle"
    actual_session_id = session_id or f"{actual_run_id}:session"
    rows = [
        _skill_hook_record(
            report=report,
            run_id=actual_run_id,
            session_id=actual_session_id,
            seq=1,
            event_type=LifecycleHookEventType.SESSION_LOAD,
            hook_id="skills.inventory.loaded",
            summary="skill inventory and lock evidence loaded",
            artifact_refs=["skills_inventory_snapshot.json", "skills_lock.json"],
        ),
        _skill_hook_record(
            report=report,
            run_id=actual_run_id,
            session_id=actual_session_id,
            seq=2,
            event_type=LifecycleHookEventType.PRE_TOOL_USE,
            hook_id="skills.selection.evidence",
            summary="skill selection decisions evaluated",
            artifact_refs=["skills_selection_decisions.json"],
        ),
    ]
    if report.invocations_recorded:
        rows.append(
            _skill_hook_record(
                report=report,
                run_id=actual_run_id,
                session_id=actual_session_id,
                seq=3,
                event_type=LifecycleHookEventType.TOOL_EVIDENCE_READY,
                hook_id="skills.invocation.provenance",
                summary="skill invocation provenance emitted",
                artifact_refs=["skills_invocation_records.json"],
            )
        )
    return rows


def project_skill_harness_adapter_events(
    report: SkillLifecycleCompatibilityReport,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    source: str = "synthetic",
) -> list[HarnessAdapterEvent]:
    """Project skill lifecycle rows into adapter-safe event rows."""
    actual_run_id = run_id or f"{_slug(report.product_family)}_skills_lifecycle"
    actual_session_id = session_id or f"{actual_run_id}:session"
    artifact_refs = project_skill_harness_artifact_refs(report)
    support_refs = project_skill_support_bundle_refs(report)
    rows: list[HarnessAdapterEvent] = []
    for seq, decision in enumerate(report.selections_made, start=1):
        rows.append(
            HarnessAdapterEvent(
                event_id=decision.decision_id,
                session_id=actual_session_id,
                run_id=actual_run_id,
                attempt_id="attempt_1",
                cursor=f"{actual_run_id}:{seq}",
                seq=seq,
                kind="skill_selection_decision",
                category="skills_lifecycle",
                state=decision.status,
                source=source,
                display={
                    "title": decision.name or decision.status,
                    "summary": decision.rationale,
                    "item_id": decision.skill_id,
                    "category": "skills_lifecycle",
                    "state": decision.status,
                },
                redacted_metadata={
                    "decision_id": decision.decision_id,
                    "digest": decision.digest,
                    "filter_reasons": decision.filter_reasons,
                    "safety_warnings": decision.safety_warnings,
                },
                artifact_refs=artifact_refs,
                support_bundle_refs=support_refs,
            )
        )
    return rows


def project_skill_harness_artifact_refs(
    report: SkillLifecycleCompatibilityReport,
) -> list[HarnessArtifactRef]:
    """Return adapter-safe artifact refs for skill lifecycle reports."""
    scenario = _primary_skill_scenario(report.product_family)
    refs = [
        HarnessArtifactRef(
            artifact_id="skills_compatibility_report.json",
            artifact_type="skill_lifecycle_compatibility_report",
            path="skills_compatibility_report.json",
            gate_id="support_bundle_artifact",
            scenario_id=scenario,
        ),
        HarnessArtifactRef(
            artifact_id="skills_inventory_snapshot.json",
            artifact_type="skill_inventory_snapshot",
            path="skills_inventory_snapshot.json",
            gate_id="deterministic_tests",
            scenario_id=scenario,
        ),
        HarnessArtifactRef(
            artifact_id="skills_lock.json",
            artifact_type="skill_lockfile",
            path="skills_lock.json",
            gate_id="deterministic_tests",
            scenario_id=scenario,
        ),
    ]
    if report.invocations_recorded:
        refs.append(
            HarnessArtifactRef(
                artifact_id="skills_invocation_records.json",
                artifact_type="skill_selection_evidence",
                path="skills_invocation_records.json",
                gate_id="support_bundle_artifact",
                scenario_id=scenario,
            )
        )
    return refs


def project_skill_support_bundle_refs(
    report: SkillLifecycleCompatibilityReport,
) -> list[HarnessSupportBundleRef]:
    """Return support-bundle refs containing compact skill lifecycle evidence."""
    return [
        HarnessSupportBundleRef(
            bundle_id=f"{report.report_id}:skills",
            bundle_type="skill_lifecycle_compatibility_report",
            path="skills_compatibility_report.json",
            gate_id="support_bundle_artifact",
            redacted_metadata={
                "report_id": report.report_id,
                "selection_count": len(report.selections_made),
                "invocation_count": len(report.invocations_recorded),
            },
        )
    ]


def build_skill_support_bundle_projection(
    report: SkillLifecycleCompatibilityReport,
) -> dict[str, Any]:
    """Build support-bundle-safe skill lifecycle payload."""
    return {
        "report_id": report.report_id,
        "product_family": report.product_family,
        "host_profile": report.host_profile,
        "inventory_snapshot_id": report.inventory_snapshot_id,
        "lock_id": report.lock_id,
        "usage_summary": report.usage_summary.model_dump(mode="json"),
        "selection_decisions": [
            decision.model_dump(mode="json") for decision in report.selections_made
        ],
        "invocation_refs": [
            invocation.model_dump(mode="json")
            for invocation in report.invocations_recorded
        ],
        "support_bundle_projection": report.support_bundle_projection,
        "no_claims": report.no_claims,
        "redaction": {
            "safe_by_default": True,
            "contains_raw_skill_body": False,
            "contains_raw_supporting_files": False,
        },
    }


def seed_excel_skill_lifecycle_report() -> SkillLifecycleCompatibilityReport:
    """Return a deterministic Excel AI skills compatibility fixture."""
    records = [
        _synthetic_record(
            skill_id="excel_ai.workbook_context",
            name="workbook-context",
            digest_seed="workbook-context",
            allowed_tools=["workbook_context"],
            compatibility={
                "product_families": ["excel_ai"],
                "required_artifacts": ["workbook_scope"],
            },
        ),
        _synthetic_record(
            skill_id="excel_ai.chart_transactions",
            name="chart-transactions",
            digest_seed="chart-transactions",
            allowed_tools=["chart_artifact", "excel_apply_edit"],
            compatibility={
                "product_families": ["excel_ai"],
                "side_effect_classes": ["transactional_edit"],
                "required_artifacts": ["chart_artifact"],
            },
            supporting_files=[
                SkillSupportingFileRef(
                    relative_path="transactions.md",
                    size_bytes=0,
                    checksum=sha256(b"transactions.md").hexdigest(),
                    kind="text",
                    source_skill_id="excel_ai.chart_transactions",
                )
            ],
        ),
        _synthetic_record(
            skill_id="excel_ai.live_provider_only",
            name="live-provider-only",
            digest_seed="live-provider-only",
            allowed_tools=["web_fetch"],
            compatibility={
                "product_families": ["excel_ai"],
                "provider_capabilities": ["live_provider"],
            },
        ),
    ]
    snapshot = _snapshot_from_records("excel-ai-skills-inventory", records)
    lockfile = build_skill_lock_file(snapshot, host_profile="excel_ai")
    request = SkillSelectionRequest(
        request_id="excel-ai-skills-selection",
        task_intent="workbook chart transaction",
        host_profile="excel_ai",
        capability_filter=SkillCapabilityFilter(
            product_family="excel_ai",
            allowed_tools=["workbook_context", "chart_artifact", "excel_apply_edit"],
            trusted_only=True,
        ),
    )
    decisions = build_skill_selection_decisions(request, records)
    return build_skill_lifecycle_compatibility_report(
        report_id="skills-lifecycle:excel-ai",
        product_family="excel_ai",
        host_profile="excel_ai",
        snapshot=snapshot,
        lockfile=lockfile,
        filters_applied=[request.capability_filter],
        selections_made=decisions,
        no_claims=[
            "live_provider_only skill remains no_claim without live provider evidence",
            "UI skill rows require Playwright evidence if changed",
        ],
        evidence_refs=[
            "skills_inventory_snapshot.json",
            "skills_lock.json",
            "skills_compatibility_report.json",
        ],
    )


def seed_chat_demo_skill_lifecycle_report() -> SkillLifecycleCompatibilityReport:
    """Return a deterministic chat-demo skills compatibility fixture."""
    snapshot = build_skill_inventory_snapshot(
        base_dir=curated_skills_dir(),
        trusted_roots=(curated_skills_dir(),),
        snapshot_id="chat-demo-research-skills-inventory",
    )
    lockfile = build_skill_lock_file(snapshot, host_profile="chat_demo")
    request = SkillSelectionRequest(
        request_id="chat-demo-research-skills-selection",
        task_intent="deep research source-grounded report",
        host_profile="chat_demo",
        capability_filter=SkillCapabilityFilter(
            product_family="chat_demo",
            allowed_tools=[
                "agent_tool",
                "artifact_preview",
                "artifact_read",
                "artifact_list",
                "browser_read",
                "file_edit",
                "file_patch",
                "file_write",
                "pdf_read",
                "read_file",
                "source_read",
                "todo_write",
                "web_fetch",
                "web_search",
            ],
            trusted_only=True,
        ),
    )
    decisions = build_skill_selection_decisions(request, snapshot.manifest_refs)
    return build_skill_lifecycle_compatibility_report(
        report_id="skills-lifecycle:chat-demo",
        product_family="chat_demo",
        host_profile="chat_demo",
        snapshot=snapshot,
        lockfile=lockfile,
        filters_applied=[request.capability_filter],
        selections_made=decisions,
        no_claims=[
            "live research/provider/Phoenix evidence is no_claim unless executed",
            "UI skill rows require Playwright evidence if changed",
        ],
        evidence_refs=[
            "skills_inventory_snapshot.json",
            "skills_lock.json",
            "skills_compatibility_report.json",
        ],
    )


def _synthetic_record(
    *,
    skill_id: str,
    name: str,
    digest_seed: str,
    allowed_tools: list[str],
    compatibility: dict[str, list[str]],
    supporting_files: list[SkillSupportingFileRef] | None = None,
) -> SkillInventoryRecord:
    return SkillInventoryRecord(
        skill_id=skill_id,
        name=name,
        digest=sha256(digest_seed.encode("utf-8")).hexdigest(),
        source="host_bundle",
        source_ref="excel_ai_fixture",
        trusted=True,
        allowed_tools=allowed_tools,
        compatibility=compatibility,
        supporting_files=list(supporting_files or []),
        metadata={"description": f"Fixture skill for {name}"},
    )


def _snapshot_from_records(
    snapshot_id: str, records: list[SkillInventoryRecord]
) -> SkillInventorySnapshot:
    digest = _stable_digest([record.model_dump(mode="json") for record in records])
    return SkillInventorySnapshot(
        snapshot_id=snapshot_id,
        root_refs=["host_bundle"],
        trusted_roots=["host_bundle"],
        returned_count=len(records),
        manifest_refs=records,
        digest=digest,
    )


def _skill_hook_record(
    *,
    report: SkillLifecycleCompatibilityReport,
    run_id: str,
    session_id: str,
    seq: int,
    event_type: LifecycleHookEventType,
    hook_id: str,
    summary: str,
    artifact_refs: list[str],
) -> LifecycleHookAuditRecord:
    event = LifecycleHookEvent(
        event_id=f"{run_id}:{seq}:{event_type.value}",
        event_type=event_type,
        run_id=run_id,
        attempt_id="attempt_1",
        session_id=session_id,
        seq=seq,
        source_component="skills_lifecycle",
        subject_summary=summary,
        artifact_refs=artifact_refs,
        redacted_metadata={
            "report_id": report.report_id,
            "product_family": report.product_family,
            "selection_count": len(report.selections_made),
            "invocation_count": len(report.invocations_recorded),
        },
    )
    result = LifecycleHookResult(
        hook_id=hook_id,
        verdict=LifecycleHookVerdict.OBSERVE,
        elapsed_ms=0.0,
        action_metadata={"skills_lifecycle": True},
    )
    return LifecycleHookAuditRecord(
        audit_id=f"{event.event_id}:{hook_id}",
        event=event,
        result=result,
        status=LifecycleHookAuditStatus.COMPLETED,
        artifact_refs=artifact_refs,
        created_at=datetime(2026, 7, 3, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
    )
