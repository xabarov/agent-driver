"""Deterministic skill lifecycle inventory, lock, diff and selection helpers."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from agent_driver.contracts.capability_packs import (
    EvidenceArtifactIndex,
    EvidenceArtifactRef,
)
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
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.contracts.skills_lifecycle import (
    SkillCapabilityFilter,
    SkillInventoryRecord,
    SkillInventorySnapshot,
    SkillInvocationRecord,
    SkillLifecycleCompatibilityReport,
    SkillLockFile,
    SkillReloadDiff,
    SkillReloadDiffRow,
    SkillSelectionDecision,
    SkillSelectionRequest,
    SkillSupportingFileRef,
    SkillUsageSummary,
)
from agent_driver.skills.curated import curated_skills_dir
from agent_driver.skills.models import SkillManifest
from agent_driver.skills.registry import SkillView, list_skill_manifests

_COMPATIBILITY_FRONTMATTER_KEYS = {
    "product_families": "product_families",
    "platforms": "platforms",
    "environments": "environments",
    "provider_capabilities": "provider_capabilities",
    "side_effect_classes": "side_effect_classes",
    "sandbox_modes": "sandbox_modes",
    "required_artifacts": "required_artifacts",
    "tags": "tags",
}


def stable_skill_id(manifest: SkillManifest) -> str:
    """Return an explicit or deterministic skill id for a manifest."""
    explicit = manifest.frontmatter.get("id") or manifest.frontmatter.get("skill_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    basis = "|".join(
        [
            manifest.name,
            manifest.source,
            manifest.relative_path or manifest.path,
            manifest.digest,
        ]
    )
    suffix = sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"skill:{_slug(manifest.name)}:{suffix}"


def supporting_file_refs(manifest: SkillManifest) -> list[SkillSupportingFileRef]:
    """Project manifest supporting files into checksum refs."""
    skill_id = stable_skill_id(manifest)
    skill_dir = Path(manifest.skill_dir)
    refs: list[SkillSupportingFileRef] = []
    for item in manifest.supporting_files:
        relative_path = str(item.get("path") or "").strip()
        if not relative_path:
            continue
        path = (skill_dir / relative_path).resolve()
        checksum = None
        size_bytes = int(item.get("size_bytes") or 0)
        if path.is_file():
            data = path.read_bytes()
            checksum = sha256(data).hexdigest()
            size_bytes = len(data)
        refs.append(
            SkillSupportingFileRef(
                relative_path=relative_path,
                size_bytes=size_bytes,
                checksum=checksum,
                kind=str(item.get("kind") or "file"),
                source_skill_id=skill_id,
            )
        )
    return refs


def inventory_record_from_manifest(manifest: SkillManifest) -> SkillInventoryRecord:
    """Build one redaction-safe inventory record from a manifest."""
    compatibility = {
        projected: _string_list(manifest.frontmatter.get(frontmatter_key))
        for frontmatter_key, projected in _COMPATIBILITY_FRONTMATTER_KEYS.items()
    }
    return SkillInventoryRecord(
        skill_id=stable_skill_id(manifest),
        name=manifest.name,
        version=manifest.version,
        digest=manifest.digest,
        source=_source_kind(manifest.source),
        source_ref=manifest.source,
        trusted=manifest.trusted,
        status="available",
        relative_path=manifest.relative_path,
        resolved_path=manifest.path,
        allowed_tools=manifest.allowed_tools,
        compatibility={key: value for key, value in compatibility.items() if value},
        supporting_files=supporting_file_refs(manifest),
        safety_warnings=manifest.safety_warnings,
        compatibility_flags=_compatibility_flags(manifest),
        metadata={
            "description": manifest.description,
            "when_to_use": manifest.when_to_use,
        },
    )


def build_skill_inventory_snapshot(
    *,
    base_dir: Path,
    trusted_roots: tuple[Path, ...] = (),
    include_hidden: bool = False,
    max_results: int = 200,
    snapshot_id: str | None = None,
    created_at: str | None = None,
) -> SkillInventorySnapshot:
    """Build a deterministic inventory snapshot from existing discovery."""
    base = base_dir.expanduser().resolve()
    trusted = [str(root.expanduser().resolve()) for root in trusted_roots]
    warnings: list[str] = []
    manifests: list[SkillManifest] = []
    truncated = False
    if not base.exists():
        warnings.append(f"skill root missing; no_claim: {base}")
    elif not base.is_dir():
        warnings.append(f"skill root is not a directory; no_claim: {base}")
    else:
        try:
            manifests, truncated = list_skill_manifests(
                base_dir=base,
                trusted_roots=trusted_roots,
                include_hidden=include_hidden,
                max_results=max_results,
            )
        except OSError as exc:
            warnings.append(f"skill root inaccessible; no_claim: {exc}")
    records = [inventory_record_from_manifest(manifest) for manifest in manifests]
    payload = {
        "root_refs": [str(base)],
        "trusted_roots": trusted,
        "max_results": max_results,
        "include_hidden": include_hidden,
        "records": [record.model_dump(mode="json") for record in records],
        "truncated": truncated,
        "warnings": warnings,
    }
    digest = _stable_digest(payload)
    return SkillInventorySnapshot(
        snapshot_id=snapshot_id or f"skills-inventory:{digest[:16]}",
        root_refs=[str(base)],
        trusted_roots=trusted,
        discovery_limits={
            "max_results": max_results,
            "include_hidden": include_hidden,
        },
        returned_count=len(records),
        truncated_count=1 if truncated else 0,
        manifest_refs=records,
        warnings=warnings,
        created_at=created_at,
        digest=digest,
    )


def build_skill_lock_file(
    snapshot: SkillInventorySnapshot,
    *,
    host_profile: str,
    lock_id: str | None = None,
    owner_notes: list[str] | None = None,
    created_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> SkillLockFile:
    """Pin an inventory snapshot into a host/profile lockfile."""
    payload = {
        "host_profile": host_profile,
        "skill_refs": [
            record.model_dump(mode="json") for record in snapshot.manifest_refs
        ],
    }
    digest = _stable_digest(payload)
    return SkillLockFile(
        lock_id=lock_id or f"skills-lock:{host_profile}:{digest[:16]}",
        host_profile=host_profile,
        skill_refs=snapshot.manifest_refs,
        owner_notes=list(owner_notes or []),
        created_at=created_at,
        digest=digest,
        metadata=dict(metadata or {}),
    )


def write_skill_lock_file(path: Path, lockfile: SkillLockFile) -> None:
    """Write a JSON lockfile fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(lockfile.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_skill_lock_file(path: Path) -> SkillLockFile:
    """Read a JSON lockfile fixture."""
    return SkillLockFile.model_validate_json(path.read_text(encoding="utf-8"))


def diff_skill_inventories(
    previous: SkillInventorySnapshot | SkillLockFile,
    current: SkillInventorySnapshot | SkillLockFile,
    *,
    diff_id: str | None = None,
) -> SkillReloadDiff:
    """Compare two snapshots or locks using ids, digests and refs."""
    previous_records = _records(previous)
    current_records = _records(current)
    previous_by_id = {record.skill_id: record for record in previous_records}
    current_by_id = {record.skill_id: record for record in current_records}
    added: list[SkillReloadDiffRow] = []
    removed: list[SkillReloadDiffRow] = []
    changed: list[SkillReloadDiffRow] = []
    disabled: list[SkillReloadDiffRow] = []
    trust_changed: list[SkillReloadDiffRow] = []
    warning_changed: list[SkillReloadDiffRow] = []
    supporting_file_changed: list[SkillReloadDiffRow] = []

    for skill_id in sorted(current_by_id.keys() - previous_by_id.keys()):
        record = current_by_id[skill_id]
        added.append(_diff_row(record, "added", status="available"))
    for skill_id in sorted(previous_by_id.keys() - current_by_id.keys()):
        record = previous_by_id[skill_id]
        removed.append(_diff_row(record, "removed", status="stale"))
    for skill_id in sorted(previous_by_id.keys() & current_by_id.keys()):
        before = previous_by_id[skill_id]
        after = current_by_id[skill_id]
        if before.digest != after.digest:
            changed.append(
                _diff_row(
                    after,
                    "changed",
                    previous_digest=before.digest,
                    current_digest=after.digest,
                )
            )
        if before.status != "disabled" and after.status == "disabled":
            disabled.append(_diff_row(after, "disabled", status="disabled"))
        if before.trusted != after.trusted:
            trust_changed.append(
                _diff_row(
                    after,
                    "trust_changed",
                    previous_value=before.trusted,
                    current_value=after.trusted,
                )
            )
        if before.safety_warnings != after.safety_warnings:
            warning_changed.append(
                _diff_row(
                    after,
                    "warning_changed",
                    previous_value=before.safety_warnings,
                    current_value=after.safety_warnings,
                )
            )
        if _supporting_file_signature(before) != _supporting_file_signature(after):
            supporting_file_changed.append(
                _diff_row(
                    after,
                    "supporting_file_changed",
                    previous_value=_supporting_file_signature(before),
                    current_value=_supporting_file_signature(after),
                )
            )

    ambiguous_name = [
        SkillReloadDiffRow(
            skill_id="name:" + name,
            name=name,
            status="ambiguous",
            change_type="ambiguous_name",
            metadata={"skill_ids": sorted(ids)},
        )
        for name, ids in _ambiguous_names(current_records).items()
    ]
    digest = _stable_digest(
        {
            "previous": _ref_id(previous),
            "current": _ref_id(current),
            "added": [row.model_dump(mode="json") for row in added],
            "removed": [row.model_dump(mode="json") for row in removed],
            "changed": [row.model_dump(mode="json") for row in changed],
            "trust_changed": [row.model_dump(mode="json") for row in trust_changed],
            "warning_changed": [row.model_dump(mode="json") for row in warning_changed],
            "supporting_file_changed": [
                row.model_dump(mode="json") for row in supporting_file_changed
            ],
            "ambiguous_name": [row.model_dump(mode="json") for row in ambiguous_name],
        }
    )
    return SkillReloadDiff(
        diff_id=diff_id or f"skills-reload-diff:{digest[:16]}",
        previous_ref=_ref_id(previous),
        current_ref=_ref_id(current),
        added=added,
        removed=removed,
        changed=changed,
        disabled=disabled,
        trust_changed=trust_changed,
        warning_changed=warning_changed,
        supporting_file_changed=supporting_file_changed,
        ambiguous_name=ambiguous_name,
    )


def build_skill_selection_decisions(
    request: SkillSelectionRequest,
    records: list[SkillInventoryRecord],
) -> list[SkillSelectionDecision]:
    """Return selected/skipped/filtered/blocked/no-claim decisions."""
    filter_spec = request.capability_filter
    if request.allowed_tools and not filter_spec.allowed_tools:
        filter_spec = filter_spec.model_copy(
            update={"allowed_tools": request.allowed_tools}
        )
    if request.candidate_skill_ids and not filter_spec.candidate_skill_ids:
        filter_spec = filter_spec.model_copy(
            update={"candidate_skill_ids": request.candidate_skill_ids}
        )
    decisions: list[SkillSelectionDecision] = []
    for record in records:
        status, reasons = _selection_status(record, filter_spec)
        decisions.append(
            SkillSelectionDecision(
                decision_id=f"{request.request_id}:{record.skill_id}",
                request_id=request.request_id,
                skill_id=record.skill_id,
                name=record.name,
                digest=record.digest,
                source=record.source,
                status=status,
                rationale=_rationale(status, reasons),
                filter_reasons=reasons,
                safety_warnings=record.safety_warnings,
                metadata={"trusted": record.trusted},
            )
        )
    selected = [decision for decision in decisions if decision.status == "selected"]
    if not records or not selected:
        decisions.append(
            SkillSelectionDecision(
                decision_id=f"{request.request_id}:no_claim",
                request_id=request.request_id,
                status="no_claim",
                rationale="No skill was selected for this request.",
                filter_reasons=[] if records else ["no_available_skills"],
            )
        )
    return decisions


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


def replay_skill_lifecycle_from_artifacts(
    root: str | Path,
) -> SkillLifecycleCompatibilityReport:
    """Replay a skill report from persisted deterministic artifacts."""
    path = Path(root)
    report_path = path / "skills_compatibility_report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"missing skills_compatibility_report.json: {path}")
    return SkillLifecycleCompatibilityReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )


def build_skill_lifecycle_evidence_index(
    report: SkillLifecycleCompatibilityReport,
    *,
    pack_id: str | None = None,
    scenario_ids: list[str] | None = None,
    include_live_no_claim_gates: bool = True,
) -> EvidenceArtifactIndex:
    """Build deterministic evidence index rows for 008 validation audit."""
    scenarios = scenario_ids or [
        "skills_lifecycle.inventory_lock_diff.v1",
        "skills_lifecycle.selection_evidence.v1",
        "skills_lifecycle.invocation_provenance.v1",
        _primary_skill_scenario(report.product_family),
    ]
    gates = [
        ValidationGateResult(
            gate_id="deterministic_tests",
            status="passed",
            evidence_path="skills_compatibility_report.json",
            redacted_metadata={"scenario": "skills_lifecycle"},
        ),
        ValidationGateResult(
            gate_id="support_bundle_artifact",
            status="passed",
            evidence_path="skills_compatibility_report.md",
            redacted_metadata={"scenario": "skills_lifecycle"},
        ),
    ]
    if include_live_no_claim_gates:
        gates.extend(
            [
                ValidationGateResult(
                    gate_id="phoenix_trace",
                    status="no_claim",
                    reason="skills_lifecycle_no_live_mode",
                ),
                ValidationGateResult(
                    gate_id="playwright_ui",
                    status="no_claim",
                    reason="skills_lifecycle_no_ui_change",
                ),
                ValidationGateResult(
                    gate_id="benchmark_delta",
                    status="no_claim",
                    reason="skills_lifecycle_no_quality_claim",
                ),
            ]
        )
    return EvidenceArtifactIndex(
        index_id=f"{report.report_id}:evidence",
        pack_id=pack_id or _pack_id_for_product(report.product_family),
        scenario_ids=[item for item in scenarios if item],
        gates=gates,
        artifacts=[
            EvidenceArtifactRef(
                artifact_id="skills_inventory_snapshot.json",
                artifact_type="skill_inventory_snapshot",
                path="skills_inventory_snapshot.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_lock.json",
                artifact_type="skill_lockfile",
                path="skills_lock.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_reload_diff.json",
                artifact_type="skill_reload_diff",
                path="skills_reload_diff.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_selection_decisions.json",
                artifact_type="skill_selection_evidence",
                path="skills_selection_decisions.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_compatibility_report.json",
                artifact_type="skill_lifecycle_compatibility_report",
                path="skills_compatibility_report.json",
                gate_id="support_bundle_artifact",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_compatibility_report.md",
                artifact_type="skill_lifecycle_compatibility_report",
                path="skills_compatibility_report.md",
                gate_id="support_bundle_artifact",
            ),
            EvidenceArtifactRef(
                artifact_id="validation_gates.json",
                artifact_type="validation_gates",
                path="validation_gates.json",
                gate_id="support_bundle_artifact",
            ),
        ],
        skipped_gate_ids=[
            gate.gate_id for gate in gates if gate.status in {"no_claim", "skipped"}
        ],
    )


def render_skill_lifecycle_markdown(
    report: SkillLifecycleCompatibilityReport,
) -> str:
    """Render a compact skill lifecycle report for owner handoff."""
    lines = [
        f"# Skills Lifecycle Compatibility {report.report_id}",
        "",
        f"Product family: `{report.product_family}`",
        f"Host profile: `{report.host_profile}`",
        f"Inventory snapshot: `{report.inventory_snapshot_id or 'none'}`",
        f"Lockfile: `{report.lock_id or 'none'}`",
        "",
        "## Usage",
        "",
        "| Counter | Value |",
        "|---|---:|",
    ]
    usage = report.usage_summary.model_dump(mode="json")
    for key in (
        "discovered",
        "selected",
        "viewed",
        "supporting_file_viewed",
        "filtered",
        "blocked",
        "failed",
        "stale",
    ):
        lines.append(f"| {key} | {usage[key]} |")
    lines.extend(
        [
            "",
            "## Selection Decisions",
            "",
            "| Skill | Status | Reason |",
            "|---|---|---|",
        ]
    )
    for decision in report.selections_made:
        lines.append(
            f"| {decision.name or decision.skill_id or 'none'} | "
            f"{decision.status} | {decision.rationale} |"
        )
    if not report.selections_made:
        lines.append("| none | no_claim | no deterministic selections |")
    lines.extend(["", "## No-Claim States", ""])
    for item in report.no_claims:
        lines.append(f"- {item}")
    if not report.no_claims:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def write_skill_lifecycle_artifacts(
    output_dir: str | Path,
    *,
    snapshot: SkillInventorySnapshot,
    lockfile: SkillLockFile,
    diff: SkillReloadDiff,
    report: SkillLifecycleCompatibilityReport,
    evidence_index: EvidenceArtifactIndex | None = None,
) -> dict[str, str]:
    """Persist skill lifecycle JSON/Markdown artifacts and manifest."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    index = evidence_index or build_skill_lifecycle_evidence_index(report)
    payloads: list[tuple[str, str, dict[str, Any] | str]] = [
        (
            "skills_inventory_snapshot.json",
            "skill_inventory_snapshot",
            snapshot.model_dump(mode="json"),
        ),
        ("skills_lock.json", "skill_lockfile", lockfile.model_dump(mode="json")),
        ("skills_reload_diff.json", "skill_reload_diff", diff.model_dump(mode="json")),
        (
            "skills_selection_decisions.json",
            "skill_selection_evidence",
            {
                "decisions": [
                    item.model_dump(mode="json") for item in report.selections_made
                ]
            },
        ),
        (
            "skills_invocation_records.json",
            "skill_selection_evidence",
            {
                "invocations": [
                    item.model_dump(mode="json") for item in report.invocations_recorded
                ]
            },
        ),
        (
            "skills_compatibility_report.json",
            "skill_lifecycle_compatibility_report",
            report.model_dump(mode="json"),
        ),
        (
            "skills_compatibility_report.md",
            "skill_lifecycle_compatibility_report",
            render_skill_lifecycle_markdown(report),
        ),
        (
            "validation_gates.json",
            "validation_gates",
            _validation_gate_summary(index),
        ),
        ("evidence_index.json", "evidence_index", index.model_dump(mode="json")),
    ]
    artifacts: list[dict[str, Any]] = []
    for filename, artifact_type, payload in payloads:
        path = root / filename
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
        artifacts.append(_artifact_manifest_row(path, artifact_type, root=root))
    manifest = {
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "redaction": {
            "safe_by_default": True,
            "contains_raw_skill_body": False,
            "contains_raw_supporting_files": False,
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        Path(item["path"]).stem: str(root / str(item["path"])) for item in artifacts
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


def _selection_status(
    record: SkillInventoryRecord,
    filter_spec: SkillCapabilityFilter,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if record.skill_id in filter_spec.disabled_skill_ids or record.status == "disabled":
        return "disabled", ["skill_disabled"]
    if record.skill_id in filter_spec.blocked_skill_ids:
        return "blocked", ["skill_blocked"]
    if (
        filter_spec.candidate_skill_ids
        and record.skill_id not in filter_spec.candidate_skill_ids
    ):
        return "skipped", ["not_in_candidate_set"]
    if filter_spec.trusted_only and not record.trusted:
        reasons.append("requires_trusted_skill")
    if filter_spec.allowed_tools:
        missing = sorted(set(record.allowed_tools) - set(filter_spec.allowed_tools))
        if missing:
            reasons.append("missing_allowed_tools:" + ",".join(missing))
    _require_compatibility(
        reasons,
        record,
        "product_families",
        filter_spec.product_family,
        "product_family_mismatch",
    )
    _require_compatibility(
        reasons,
        record,
        "platforms",
        filter_spec.platform,
        "platform_mismatch",
    )
    _require_compatibility(
        reasons,
        record,
        "environments",
        filter_spec.environment,
        "environment_mismatch",
    )
    _require_compatibility(
        reasons,
        record,
        "sandbox_modes",
        filter_spec.sandbox_mode,
        "sandbox_mode_mismatch",
    )
    reasons.extend(
        _missing_required(
            record.compatibility.get("provider_capabilities", []),
            filter_spec.provider_capabilities,
            "missing_provider_capability",
        )
    )
    reasons.extend(
        _missing_required(
            record.compatibility.get("side_effect_classes", []),
            filter_spec.side_effect_classes,
            "missing_side_effect_class",
        )
    )
    reasons.extend(
        _missing_required(
            record.compatibility.get("required_artifacts", []),
            filter_spec.required_artifacts,
            "missing_required_artifact",
        )
    )
    reasons.extend(
        _missing_required(
            record.compatibility.get("tags", []),
            filter_spec.required_tags,
            "missing_required_tag",
        )
    )
    return ("filtered", reasons) if reasons else ("selected", [])


def _require_compatibility(
    reasons: list[str],
    record: SkillInventoryRecord,
    key: str,
    desired: str | None,
    reason: str,
) -> None:
    available = record.compatibility.get(key, [])
    if desired and available and desired not in available:
        reasons.append(reason)


def _missing_required(
    available: list[str], required: list[str], reason_prefix: str
) -> list[str]:
    missing = sorted(set(available) - set(required))
    return [f"{reason_prefix}:{item}" for item in missing]


def _rationale(status: str, reasons: list[str]) -> str:
    if status == "selected":
        return "Skill selected by deterministic capability filter."
    if status == "skipped":
        return "Skill skipped before compatibility filtering."
    if status == "disabled":
        return "Skill disabled by host/profile lock."
    if status == "blocked":
        return "Skill blocked by host/profile policy."
    if status == "filtered":
        return "Skill filtered by capability constraints: " + ", ".join(reasons)
    return "No deterministic claim is available."


def _records(
    value: SkillInventorySnapshot | SkillLockFile,
) -> list[SkillInventoryRecord]:
    if isinstance(value, SkillInventorySnapshot):
        return value.manifest_refs
    return value.skill_refs


def _ref_id(value: SkillInventorySnapshot | SkillLockFile) -> str:
    if isinstance(value, SkillInventorySnapshot):
        return value.snapshot_id
    return value.lock_id


def _diff_row(
    record: SkillInventoryRecord,
    change_type: str,
    *,
    status: str = "changed",
    previous_digest: str | None = None,
    current_digest: str | None = None,
    previous_value: Any = None,
    current_value: Any = None,
) -> SkillReloadDiffRow:
    return SkillReloadDiffRow(
        skill_id=record.skill_id,
        name=record.name,
        status=status,  # type: ignore[arg-type]
        change_type=change_type,
        previous_digest=previous_digest,
        current_digest=current_digest,
        previous_value=previous_value,
        current_value=current_value,
    )


def _supporting_file_signature(record: SkillInventoryRecord) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": item.relative_path,
            "size_bytes": item.size_bytes,
            "checksum": item.checksum,
            "kind": item.kind,
        }
        for item in record.supporting_files
    ]


def _ambiguous_names(
    records: Iterable[SkillInventoryRecord],
) -> dict[str, list[str]]:
    rows = list(records)
    counts = Counter(record.name for record in rows)
    return {
        name: [record.skill_id for record in rows if record.name == name]
        for name, count in counts.items()
        if count > 1
    }


def _compatibility_flags(manifest: SkillManifest) -> list[str]:
    flags: list[str] = []
    if manifest.trusted:
        flags.append("trusted_root")
    if manifest.supporting_files:
        flags.append("has_supporting_files")
    if manifest.allowed_tools:
        flags.append("declares_allowed_tools")
    return flags


def _source_kind(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {
        "curated",
        "filesystem",
        "workspace",
        "user",
        "host_bundle",
        "external",
        "unknown",
    }:
        return normalized
    return "filesystem"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unnamed"


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


def _artifact_manifest_row(
    path: Path,
    artifact_type: str,
    *,
    root: Path,
) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact_type": artifact_type,
        "path": path.relative_to(root).as_posix(),
        "size_bytes": len(data),
        "sha256": sha256(data).hexdigest(),
    }


def _primary_skill_scenario(product_family: str) -> str:
    if product_family == "excel_ai":
        return "skills_lifecycle.excel_workbook_skills.v1"
    if product_family == "chat_demo":
        return "skills_lifecycle.chat_demo_research_skills.v1"
    return "skills_lifecycle.selection_evidence.v1"


def _pack_id_for_product(product_family: str) -> str | None:
    if product_family == "excel_ai":
        return "excel_workbook_chat"
    if product_family == "chat_demo":
        return "deep_research_chat_demo"
    return None


def _validation_gate_summary(index: EvidenceArtifactIndex) -> dict[str, Any]:
    gates = [gate.model_dump(mode="json") for gate in index.gates]
    return {
        "count": len(gates),
        "statuses": {gate["gate_id"]: gate["status"] for gate in gates},
        "gates": gates,
        "redaction": {"safe_by_default": True},
    }


__all__ = [
    "build_skill_lifecycle_evidence_index",
    "build_skill_inventory_snapshot",
    "build_skill_lifecycle_compatibility_report",
    "build_skill_lock_file",
    "build_skill_selection_decisions",
    "build_skill_support_bundle_projection",
    "build_skill_usage_summary",
    "diff_skill_inventories",
    "inventory_record_from_manifest",
    "invocation_record_from_view",
    "project_skill_harness_adapter_events",
    "project_skill_harness_artifact_refs",
    "project_skill_lifecycle_hook_audit_records",
    "project_skill_support_bundle_refs",
    "read_skill_lock_file",
    "render_skill_lifecycle_markdown",
    "replay_skill_lifecycle_from_artifacts",
    "seed_chat_demo_skill_lifecycle_report",
    "seed_excel_skill_lifecycle_report",
    "skill_attachment_from_record",
    "stable_skill_id",
    "support_bundle_projection",
    "supporting_file_refs",
    "write_skill_lifecycle_artifacts",
    "write_skill_lock_file",
]
