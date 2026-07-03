"""Trace-safe provenance contracts for context, skills and side effects."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


ContextProvenanceStatus = Literal[
    "attached",
    "retained",
    "compacted",
    "dropped",
    "truncated",
    "expired",
    "missing",
]
RedactionLevel = Literal["none", "summary", "redacted", "secret"]


class ContextProvenanceRecord(ContractModel):
    """One bounded context item made visible to diagnostics."""

    context_id: str
    kind: str
    source_ref: str | None = None
    scope: str = "run"
    status: ContextProvenanceStatus = "attached"
    redaction_level: RedactionLevel = "summary"
    token_estimate: int | None = None
    compaction_policy: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    product_tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("token_estimate")
    @classmethod
    def validate_token_estimate(cls, value: int | None) -> int | None:
        return ensure_non_negative_int(value, field_name="token_estimate")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="context provenance metadata")


class ContextLedgerSummary(ContractModel):
    """Compact per-run context provenance projection."""

    records: list[ContextProvenanceRecord] = Field(default_factory=list)
    counts_by_kind: dict[str, int] = Field(default_factory=dict)
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    dropped_count: int = 0
    truncated_count: int = 0
    compacted_count: int = 0
    high_risk_missing_count: int = 0
    last_compaction_verdict: str | None = None
    redaction: dict[str, Any] = Field(
        default_factory=lambda: {
            "safe_by_default": True,
            "contains_raw_context": False,
        }
    )


class MemoryFactProvenance(ContractModel):
    """Trace-safe relationship between a memory fact and its source."""

    fact_id: str
    source_ref: str | None = None
    confidence: float | None = None
    freshness: str | None = None
    link_check_status: str | None = None
    invalidation_reason: str | None = None
    compaction_survival_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="memory fact metadata")


class SkillAttachment(ContractModel):
    """Skill attachment/version provenance without raw skill content."""

    skill_id: str
    name: str
    version: str | None = None
    source: str = "filesystem"
    attachment_scope: str = "run"
    activation_reason: str | None = None
    status: str = "attached"
    resolved_path: str | None = None
    package_source: str | None = None
    compatibility_flags: list[str] = Field(default_factory=list)
    redacted_manifest_checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="skill attachment metadata")


class ArtifactProvenance(ContractModel):
    """Trace-safe artifact/source/workspace provenance."""

    artifact_id: str
    artifact_type: str
    source_tool: str | None = None
    source_run_id: str | None = None
    path: str | None = None
    preview_status: str | None = None
    read_status: str | None = None
    derived_from_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    quality_verdict: str | None = None
    safe_path_classification: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="artifact provenance metadata")


class SourceEvidenceRecord(ContractModel):
    """Search/fetch/source/citation lineage record."""

    source_id: str
    source_type: str
    canonical_url: str | None = None
    domain: str | None = None
    tool_call_id: str | None = None
    fetch_status: str = "observed"
    citation_status: str | None = None
    quote_coverage: str | None = None
    stale_or_missing_verdict: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="source evidence metadata")


class SideEffectTransaction(ContractModel):
    """Diagnostics-only projection for side-effecting boundaries."""

    transaction_id: str
    side_effect_class: str
    tool_name: str | None = None
    runtime_decision_id: str | None = None
    target_ref: str | None = None
    preview_status: str | None = None
    approval_status: str | None = None
    apply_status: str | None = None
    rollback_status: str | None = None
    cancel_status: str | None = None
    policy_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="side-effect metadata")


__all__ = [
    "ArtifactProvenance",
    "ContextLedgerSummary",
    "ContextProvenanceRecord",
    "ContextProvenanceStatus",
    "MemoryFactProvenance",
    "RedactionLevel",
    "SideEffectTransaction",
    "SkillAttachment",
    "SourceEvidenceRecord",
]
