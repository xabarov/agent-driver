"""Redaction-safe contracts for governed skill lifecycle evidence."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
    is_sensitive_key,
    looks_like_env_name,
)

SkillLifecycleStatus = Literal[
    "available",
    "selected",
    "viewed",
    "filtered",
    "disabled",
    "blocked",
    "ambiguous",
    "changed",
    "stale",
    "skipped",
    "no_claim",
    "failed",
]
SkillSourceKind = Literal[
    "curated",
    "filesystem",
    "workspace",
    "user",
    "host_bundle",
    "external",
    "unknown",
]
SkillRedactionStatus = Literal["redacted", "summary", "none"]
SkillReadStatus = Literal[
    "not_read",
    "read",
    "missing",
    "truncated",
    "blocked",
    "failed",
]
SkillSafetyScanStatus = Literal[
    "not_scanned",
    "passed",
    "flagged",
    "substituted",
    "failed",
]

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")
_RAW_CONTENT_MARKERS = (
    "body",
    "content",
    "raw",
    "prompt",
    "skill_text",
    "skill_body",
)


def _assert_redaction_safe(value: Any, *, path: str = "") -> None:
    """Reject secret-shaped values and raw skill content fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            lower = key_text.lower()
            next_path = f"{path}.{key_text}" if path else key_text
            if is_sensitive_key(key_text, markers=_SECRET_KEY_MARKERS):
                if not (isinstance(item, str) and looks_like_env_name(item)):
                    raise ValueError(
                        "skill lifecycle metadata may name secret env vars but "
                        f"must not contain secret values: {next_path}"
                    )
            if any(
                marker == lower or lower.endswith(f"_{marker}")
                for marker in _RAW_CONTENT_MARKERS
            ):
                raise ValueError(
                    "skill lifecycle reports must not include raw skill contents: "
                    f"{next_path}"
                )
            _assert_redaction_safe(item, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_redaction_safe(item, path=f"{path}[{index}]")


def _validate_json_safe_metadata(
    value: dict[str, Any], *, field_name: str
) -> dict[str, Any]:
    value = ensure_json_serializable(value, field_name=field_name)
    _assert_redaction_safe(value)
    return value


class SkillSupportingFileRef(ContractModel):
    """Reference to one supporting file without embedding its contents."""

    relative_path: str
    size_bytes: int = 0
    checksum: str | None = None
    kind: str = "file"
    read_status: SkillReadStatus = "not_read"
    safety_scan_status: SkillSafetyScanStatus = "not_scanned"
    source_skill_id: str | None = None
    redaction_status: SkillRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("size_bytes")
    @classmethod
    def validate_size_bytes(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="size_bytes") or 0

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill supporting file metadata"
        )


class SkillInventoryRecord(ContractModel):
    """One skill row captured in an inventory snapshot or lockfile."""

    skill_id: str
    name: str
    version: str | None = None
    digest: str
    source: SkillSourceKind = "filesystem"
    source_ref: str | None = None
    trusted: bool = False
    status: SkillLifecycleStatus = "available"
    relative_path: str | None = None
    resolved_path: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    compatibility: dict[str, list[str]] = Field(default_factory=dict)
    supporting_files: list[SkillSupportingFileRef] = Field(default_factory=list)
    safety_warnings: list[str] = Field(default_factory=list)
    compatibility_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill inventory metadata"
        )

    @field_validator("compatibility")
    @classmethod
    def validate_compatibility(
        cls, value: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        return {
            str(key): [str(item) for item in items if str(item).strip()]
            for key, items in value.items()
        }


class SkillInventorySnapshot(ContractModel):
    """Deterministic scan of configured skill roots."""

    snapshot_id: str
    root_refs: list[str] = Field(default_factory=list)
    trusted_roots: list[str] = Field(default_factory=list)
    discovery_limits: dict[str, Any] = Field(default_factory=dict)
    returned_count: int = 0
    truncated_count: int = 0
    manifest_refs: list[SkillInventoryRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    digest: str
    redaction_status: SkillRedactionStatus = "redacted"

    @field_validator("discovery_limits")
    @classmethod
    def validate_discovery_limits(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill inventory discovery limits"
        )

    @model_validator(mode="after")
    def validate_unique_skill_ids(self) -> "SkillInventorySnapshot":
        _assert_unique_skill_ids(self.manifest_refs)
        return self


class SkillLockFile(ContractModel):
    """Pinned skill inventory for one host or profile."""

    lock_id: str
    host_profile: str
    skill_refs: list[SkillInventoryRecord] = Field(default_factory=list)
    created_at: str | None = None
    owner_notes: list[str] = Field(default_factory=list)
    digest: str
    redaction_status: SkillRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="skill lock metadata")

    @model_validator(mode="after")
    def validate_unique_skill_ids(self) -> "SkillLockFile":
        _assert_unique_skill_ids(self.skill_refs)
        return self


class SkillReloadDiffRow(ContractModel):
    """One deterministic inventory or lockfile diff row."""

    skill_id: str
    name: str | None = None
    status: SkillLifecycleStatus = "changed"
    change_type: str
    previous_digest: str | None = None
    current_digest: str | None = None
    previous_value: Any = None
    current_value: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="skill diff metadata")


class SkillReloadDiff(ContractModel):
    """Comparison between two inventory snapshots or lockfiles."""

    diff_id: str
    previous_ref: str | None = None
    current_ref: str | None = None
    added: list[SkillReloadDiffRow] = Field(default_factory=list)
    removed: list[SkillReloadDiffRow] = Field(default_factory=list)
    changed: list[SkillReloadDiffRow] = Field(default_factory=list)
    disabled: list[SkillReloadDiffRow] = Field(default_factory=list)
    trust_changed: list[SkillReloadDiffRow] = Field(default_factory=list)
    warning_changed: list[SkillReloadDiffRow] = Field(default_factory=list)
    supporting_file_changed: list[SkillReloadDiffRow] = Field(default_factory=list)
    ambiguous_name: list[SkillReloadDiffRow] = Field(default_factory=list)
    redaction_status: SkillRedactionStatus = "redacted"


class SkillCapabilityFilter(ContractModel):
    """Host/profile constraints used to filter skills deterministically."""

    product_family: str | None = None
    platform: str | None = None
    environment: str | None = None
    provider_capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    side_effect_classes: list[str] = Field(default_factory=list)
    sandbox_mode: str | None = None
    trusted_only: bool = False
    required_artifacts: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)
    candidate_skill_ids: list[str] = Field(default_factory=list)
    disabled_skill_ids: list[str] = Field(default_factory=list)
    blocked_skill_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="skill filter metadata")


class SkillSelectionRequest(ContractModel):
    """Deterministic request describing why skill selection was evaluated."""

    request_id: str
    task_intent: str
    available_capabilities: list[str] = Field(default_factory=list)
    host_profile: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    selected_provider_route: str | None = None
    budget_constraints: dict[str, Any] = Field(default_factory=dict)
    context_constraints: dict[str, Any] = Field(default_factory=dict)
    candidate_skill_ids: list[str] = Field(default_factory=list)
    capability_filter: SkillCapabilityFilter = Field(
        default_factory=SkillCapabilityFilter
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("budget_constraints", "context_constraints", "metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill selection request metadata"
        )


class SkillSelectionDecision(ContractModel):
    """Compact selection evidence without raw skill contents."""

    decision_id: str
    request_id: str
    status: SkillLifecycleStatus
    skill_id: str | None = None
    name: str | None = None
    digest: str | None = None
    source: SkillSourceKind | None = None
    rationale: str = ""
    filter_reasons: list[str] = Field(default_factory=list)
    safety_warnings: list[str] = Field(default_factory=list)
    redaction_status: SkillRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_decision_status(
        cls, value: SkillLifecycleStatus
    ) -> SkillLifecycleStatus:
        if value not in {
            "selected",
            "skipped",
            "filtered",
            "disabled",
            "blocked",
            "ambiguous",
            "no_claim",
            "failed",
        }:
            raise ValueError(f"unsupported skill selection status: {value}")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill selection decision metadata"
        )


class SkillInvocationRecord(ContractModel):
    """Expanded telemetry for viewing a skill body or supporting file."""

    invocation_id: str
    skill_id: str
    name: str
    digest: str
    content_kind: str
    supporting_file: SkillSupportingFileRef | None = None
    truncated: bool = False
    safety_scan_status: SkillSafetyScanStatus = "not_scanned"
    tool_call_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    provenance_refs: list[str] = Field(default_factory=list)
    redaction_status: SkillRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill invocation metadata"
        )


class SkillUsageSummary(ContractModel):
    """Deterministic skill usage counters and optional outcome linkage."""

    discovered: int = 0
    selected: int = 0
    viewed: int = 0
    supporting_file_viewed: int = 0
    filtered: int = 0
    blocked: int = 0
    failed: int = 0
    stale: int = 0
    outcome_links: dict[str, Any] = Field(default_factory=dict)
    redaction_status: SkillRedactionStatus = "redacted"

    @field_validator(
        "discovered",
        "selected",
        "viewed",
        "supporting_file_viewed",
        "filtered",
        "blocked",
        "failed",
        "stale",
    )
    @classmethod
    def validate_counter(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="skill usage counter") or 0

    @field_validator("outcome_links")
    @classmethod
    def validate_outcome_links(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(
            value, field_name="skill usage outcome links"
        )


class SkillLifecycleCompatibilityReport(ContractModel):
    """Host/product compatibility projection for governed skills."""

    report_id: str
    product_family: str
    host_profile: str
    inventory_snapshot_id: str | None = None
    lock_id: str | None = None
    roots_scanned: list[str] = Field(default_factory=list)
    locks_verified: list[str] = Field(default_factory=list)
    filters_applied: list[SkillCapabilityFilter] = Field(default_factory=list)
    selections_made: list[SkillSelectionDecision] = Field(default_factory=list)
    invocations_recorded: list[SkillInvocationRecord] = Field(default_factory=list)
    usage_summary: SkillUsageSummary = Field(default_factory=SkillUsageSummary)
    provenance_rows_emitted: list[dict[str, Any]] = Field(default_factory=list)
    no_claims: list[str] = Field(default_factory=list)
    support_bundle_projection: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    redaction_status: SkillRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provenance_rows_emitted", "support_bundle_projection")
    @classmethod
    def validate_report_rows(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            _validate_json_safe_metadata(item, field_name="skill report row")
            for item in value
        ]

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="skill report metadata")


def _assert_unique_skill_ids(records: list[SkillInventoryRecord]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        if record.skill_id in seen:
            duplicates.add(record.skill_id)
        seen.add(record.skill_id)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate skill ids: {joined}")


__all__ = [
    "SkillCapabilityFilter",
    "SkillInventoryRecord",
    "SkillInventorySnapshot",
    "SkillInvocationRecord",
    "SkillLifecycleCompatibilityReport",
    "SkillLifecycleStatus",
    "SkillLockFile",
    "SkillReadStatus",
    "SkillRedactionStatus",
    "SkillReloadDiff",
    "SkillReloadDiffRow",
    "SkillSafetyScanStatus",
    "SkillSelectionDecision",
    "SkillSelectionRequest",
    "SkillSourceKind",
    "SkillSupportingFileRef",
    "SkillUsageSummary",
]
