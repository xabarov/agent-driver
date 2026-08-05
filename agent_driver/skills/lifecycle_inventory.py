"""Skill inventory, lock file, diff, and selection decisions.

Split out of ``skills/lifecycle.py`` (god-module split, behaviour-neutral);
re-exported from ``lifecycle`` for existing callers.
"""


from __future__ import annotations

import json
import re
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from agent_driver.contracts.skills_lifecycle import (
    SkillCapabilityFilter,
    SkillInventoryRecord,
    SkillInventorySnapshot,
    SkillLockFile,
    SkillReloadDiff,
    SkillReloadDiffRow,
    SkillSelectionDecision,
    SkillSelectionRequest,
    SkillSupportingFileRef,
)
from agent_driver.skills.models import SkillManifest
from agent_driver.skills.registry import list_skill_manifests


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
