"""Durable lifecycle contracts for sessions, runs, checkpoints and plans."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import StrEnum
from agent_driver.contracts.validation import (
    assert_no_secret_fields,
    ensure_json_serializable,
    ensure_non_negative_int,
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)


class DurableLifecycleStatus(StrEnum):
    """Canonical durable run/session lifecycle states."""

    UNKNOWN = "unknown"
    CREATED = "created"
    QUEUED = "queued"
    ACTIVE = "active"
    PAUSED = "paused"
    RECOVERABLE = "recoverable"
    ORPHANED = "orphaned"
    STALE = "stale"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DurableInterruptStatus(StrEnum):
    """Durable interrupt state vocabulary."""

    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class DurableApprovalStatus(StrEnum):
    """Durable approval request/response state vocabulary."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"
    CLARIFY = "clarify"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class DurableLeaseStatus(StrEnum):
    """Background run lease state vocabulary."""

    ACTIVE = "active"
    STALE = "stale"
    ORPHANED = "orphaned"
    RELEASED = "released"


class DurablePlanVerdict(StrEnum):
    """Verdicts for attach, resume and fork planning."""

    ATTACH_LIVE = "attach_live"
    REPLAY_ONLY = "replay_only"
    ORPHANED = "orphaned"
    NOT_FOUND = "not_found"
    TERMINAL = "terminal"
    RESUME_AVAILABLE = "resume_available"
    APPROVAL_REQUIRED = "approval_required"
    CHECKPOINT_MISSING = "checkpoint_missing"
    SIDE_EFFECT_UNSAFE = "side_effect_unsafe"
    STORAGE_UNSUPPORTED = "storage_unsupported"
    FORK_AVAILABLE = "fork_available"
    UNSUPPORTED = "unsupported"
    SKIPPED = "skipped"
    STALE = "stale"
    NO_CLAIM = "no_claim"
    FAILED = "failed"


class DurableDurabilityLevel(StrEnum):
    """Durability levels shared with adapter compatibility plus runtime stores."""

    PROCESS_LOCAL = "process_local"
    SQLITE = "sqlite"
    JSONL = "jsonl"
    POSTGRES = "postgres"
    EXTERNAL_DB = "external_db"
    MANAGED_HOST = "managed_host"
    UNKNOWN = "unknown"


class DurableSideEffectSafety(StrEnum):
    """Truthful side-effect resumability evidence state."""

    SAFE = "safe"
    UNSAFE = "unsafe"
    NO_CLAIM = "no_claim"
    MISSING = "missing"


class BackgroundRunLogRef(ContractModel):
    """Append-only log or support artifact pointer."""

    log_id: str
    run_id: str
    log_type: str
    path: str | None = None
    uri: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    redaction_status: str = "redacted"
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("log_id", "run_id", "log_type", "redaction_status")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("size_bytes")
    @classmethod
    def validate_size(cls, value: int | None) -> int | None:
        return ensure_non_negative_int(value, field_name="size_bytes")

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="background log metadata")


class DurableSessionRecord(ContractModel):
    """Canonical durable session row."""

    session_id: str
    workspace_id: str | None = None
    owner_id: str | None = None
    adapter_id: str | None = None
    current_run_id: str | None = None
    lifecycle_state: DurableLifecycleStatus = DurableLifecycleStatus.UNKNOWN
    created_at: str | None = None
    updated_at: str | None = None
    transcript_available: bool = False
    durability_level: DurableDurabilityLevel = DurableDurabilityLevel.UNKNOWN
    search_metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    support_bundle_refs: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("search_metadata", "redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable session metadata")


class DurableRunRecord(ContractModel):
    """Canonical durable run row."""

    run_id: str
    session_id: str
    attempt_id: str = "attempt_1"
    status: DurableLifecycleStatus = DurableLifecycleStatus.CREATED
    active_lease_id: str | None = None
    latest_seq: int = 0
    reconnect_cursor: str | None = None
    latest_checkpoint_id: str | None = None
    paused_interrupt_id: str | None = None
    abort_request_id: str | None = None
    terminal_verdict: str | None = None
    durability_level: DurableDurabilityLevel = DurableDurabilityLevel.PROCESS_LOCAL
    side_effect_safety: DurableSideEffectSafety = DurableSideEffectSafety.NO_CLAIM
    support_bundle_refs: list[str] = Field(default_factory=list)
    log_refs: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("run_id", "session_id", "attempt_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("latest_seq")
    @classmethod
    def validate_latest_seq(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="latest_seq") or value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable run metadata")

    @model_validator(mode="after")
    def validate_cursor(self) -> "DurableRunRecord":
        if self.reconnect_cursor and self.latest_seq:
            expected = f"{self.run_id}:{self.latest_seq}"
            if self.reconnect_cursor != expected:
                raise ValueError(f"reconnect_cursor must be stable run:seq: {expected}")
        return self


class DurableCheckpointIndex(ContractModel):
    """Indexed checkpoint metadata separate from resume claims."""

    checkpoint_id: str
    run_id: str
    attempt_id: str = "attempt_1"
    parent_checkpoint_id: str | None = None
    branch_id: str | None = None
    graph_id: str
    node_id: str | None = None
    state_version: str
    storage_backend: DurableDurabilityLevel
    resumable: bool = False
    forkable: bool = False
    side_effect_safety: DurableSideEffectSafety = DurableSideEffectSafety.NO_CLAIM
    side_effect_notes: str | None = None
    created_at: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "checkpoint_id", "run_id", "attempt_id", "graph_id", "state_version"
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable checkpoint metadata")


class DurableInterruptRecord(ContractModel):
    """JSON-safe pending/resolved interrupt record."""

    interrupt_id: str
    run_id: str
    checkpoint_id: str | None = None
    status: DurableInterruptStatus = DurableInterruptStatus.PENDING
    reason: str
    allowed_actions: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    approval_payload_summary: dict[str, Any] = Field(default_factory=dict)
    resolution: dict[str, Any] | None = None
    resolver_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    resolved_at: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("interrupt_id", "run_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator(
        "approval_payload_summary",
        "resolver_metadata",
        "redacted_metadata",
    )
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable interrupt metadata")

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_redacted_json(value, field_name="durable interrupt resolution")

    @model_validator(mode="after")
    def validate_resolution_state(self) -> "DurableInterruptRecord":
        if self.status != DurableInterruptStatus.PENDING and self.resolved_at is None:
            raise ValueError("resolved interrupt records must include resolved_at")
        return self


class DurableApprovalRecord(ContractModel):
    """Normalized approval request/response row."""

    approval_id: str
    interrupt_id: str | None = None
    run_id: str
    status: DurableApprovalStatus = DurableApprovalStatus.PENDING
    requested_action: str | None = None
    response_action: str | None = None
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] | None = None
    requested_at: str | None = None
    resolved_at: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("approval_id", "run_id")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("request_summary", "redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable approval metadata")

    @field_validator("response_summary")
    @classmethod
    def validate_response(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _validate_redacted_json(value, field_name="durable approval response")


class BackgroundRunLease(ContractModel):
    """Process/worker lease for an active background run."""

    lease_id: str
    run_id: str
    owner_process_id: str | None = None
    owner_host_id: str | None = None
    heartbeat_seq: int = 0
    heartbeat_at: str | None = None
    expires_at: str | None = None
    status: DurableLeaseStatus = DurableLeaseStatus.ACTIVE
    takeover_policy: str = "manual"
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("lease_id", "run_id", "takeover_policy")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("heartbeat_seq")
    @classmethod
    def validate_seq(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="heartbeat_seq") or value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="background lease metadata")


class DurableAbortRequestRecord(ContractModel):
    """Durable abort request marker for recovery diagnostics."""

    abort_request_id: str
    run_id: str
    reason: str
    requested_at: str | None = None
    requested_by: str | None = None
    observed: bool = False
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("abort_request_id", "run_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable abort metadata")


class AttachPlan(ContractModel):
    """Verdict for attaching to a durable run/session."""

    verdict: DurablePlanVerdict
    run_id: str | None = None
    session_id: str | None = None
    lease_id: str | None = None
    replay_cursor: str | None = None
    latest_seq: int = 0
    reason: str | None = None
    can_replay: bool = False
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("latest_seq")
    @classmethod
    def validate_seq(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="latest_seq") or value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="attach plan metadata")


class ResumePlan(ContractModel):
    """Verdict for resuming a paused/recoverable durable run."""

    verdict: DurablePlanVerdict
    run_id: str | None = None
    session_id: str | None = None
    checkpoint_id: str | None = None
    interrupt_id: str | None = None
    approval_id: str | None = None
    reason: str | None = None
    storage_backend: DurableDurabilityLevel = DurableDurabilityLevel.UNKNOWN
    side_effect_safety: DurableSideEffectSafety = DurableSideEffectSafety.NO_CLAIM
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="resume plan metadata")


class ForkPlan(ContractModel):
    """Verdict for forking a session, run or checkpoint into a new branch."""

    verdict: DurablePlanVerdict
    source_session_id: str | None = None
    source_run_id: str | None = None
    source_checkpoint_id: str | None = None
    new_session_id: str | None = None
    new_branch_id: str | None = None
    parent_branch_id: str | None = None
    copied_artifact_refs: list[str] = Field(default_factory=list)
    reason: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="fork plan metadata")

    @model_validator(mode="after")
    def validate_fork_ids(self) -> "ForkPlan":
        if self.verdict == DurablePlanVerdict.FORK_AVAILABLE:
            if not self.new_session_id or not self.new_branch_id:
                raise ValueError("available fork plans must include new ids")
        return self


class DurableLifecycleCompatibilityReport(ContractModel):
    """Deterministic report for durable lifecycle claims."""

    report_id: str
    generated_at: str
    product_family: str
    protocol: str = "durable_lifecycle"
    no_live: bool = True
    feature_statuses: dict[str, str] = Field(default_factory=dict)
    session_records: list[DurableSessionRecord] = Field(default_factory=list)
    run_records: list[DurableRunRecord] = Field(default_factory=list)
    checkpoint_records: list[DurableCheckpointIndex] = Field(default_factory=list)
    interrupt_records: list[DurableInterruptRecord] = Field(default_factory=list)
    approval_records: list[DurableApprovalRecord] = Field(default_factory=list)
    abort_records: list[DurableAbortRequestRecord] = Field(default_factory=list)
    lease_records: list[BackgroundRunLease] = Field(default_factory=list)
    log_refs: list[BackgroundRunLogRef] = Field(default_factory=list)
    attach_plans: list[AttachPlan] = Field(default_factory=list)
    resume_plans: list[ResumePlan] = Field(default_factory=list)
    fork_plans: list[ForkPlan] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    validation_gate_statuses: dict[str, str] = Field(default_factory=dict)
    skipped_reasons: dict[str, str] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(default_factory=lambda: {"safe_by_default": True})
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("report_id", "generated_at", "product_family", "protocol")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("feature_statuses", "validation_gate_statuses")
    @classmethod
    def validate_statuses(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {item.value for item in DurablePlanVerdict} | {
            "supported",
            "unsupported",
            "skipped",
            "stale",
            "no_claim",
            "failed",
            "passed",
        }
        for key, status in value.items():
            if status not in allowed:
                raise ValueError(
                    f"unsupported durable lifecycle status for {key}: {status}"
                )
        return value

    @field_validator("skipped_reasons", "redaction", "redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_redacted_json(value, field_name="durable report metadata")


def _required_text(value: str) -> str:
    if not value.strip():
        raise ValueError("durable lifecycle text fields must be non-empty")
    return value


def _validate_redacted_json(value: Any, *, field_name: str) -> Any:
    safe = ensure_json_serializable(value, field_name=field_name)
    _assert_no_secret_fields(safe, path=field_name)
    return safe


def _assert_no_secret_fields(value: Any, *, path: str) -> None:
    assert_no_secret_fields(
        value,
        subject="durable lifecycle metadata",
        path=path,
        markers=_SECRET_KEY_MARKERS,
        value_pattern=_SECRET_VALUE_RE,
    )


__all__ = [
    "AttachPlan",
    "BackgroundRunLease",
    "BackgroundRunLogRef",
    "DurableAbortRequestRecord",
    "DurableApprovalRecord",
    "DurableApprovalStatus",
    "DurableCheckpointIndex",
    "DurableDurabilityLevel",
    "DurableInterruptRecord",
    "DurableInterruptStatus",
    "DurableLeaseStatus",
    "DurableLifecycleCompatibilityReport",
    "DurableLifecycleStatus",
    "DurablePlanVerdict",
    "DurableRunRecord",
    "DurableSessionRecord",
    "DurableSideEffectSafety",
    "ForkPlan",
    "ResumePlan",
]
