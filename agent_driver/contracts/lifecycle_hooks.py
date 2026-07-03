"""Lifecycle hook middleware contracts.

These models define the metadata plane around existing tool hooks, runtime
lifecycle hooks and declarative hook chains. They are intentionally inert:
hosts can record deterministic audit rows and compatibility reports without
changing run behavior.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import StrEnum
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_non_negative_int,
    ensure_positive_float,
    ensure_positive_int,
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)
_COMPAT_STATUSES = frozenset(
    {"supported", "unsupported", "no_claim", "skipped", "stale"}
)


class LifecycleHookEventType(StrEnum):
    """Stable lifecycle event vocabulary for middleware hooks."""

    SESSION_START = "session_start"
    SESSION_LOAD = "session_load"
    RUN_START = "run_start"
    BEFORE_LLM_REQUEST = "before_llm_request"
    AFTER_LLM_RESPONSE = "after_llm_response"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    TOOL_EVIDENCE_READY = "tool_evidence_ready"
    INTERRUPT_REQUESTED = "interrupt_requested"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    ARTIFACT_CREATED = "artifact_created"
    FILE_CHANGED = "file_changed"
    RUN_FINALIZE = "run_finalize"
    RUN_STOP = "run_stop"
    RUN_ERROR = "run_error"


class LifecycleHookVerdict(StrEnum):
    """Typed lifecycle middleware outcome vocabulary."""

    ALLOW = "allow"
    OBSERVE = "observe"
    TRANSFORM = "transform"
    BLOCK = "block"
    REQUEST_APPROVAL = "request_approval"
    EMIT_WARNING = "emit_warning"
    FINALIZE = "finalize"
    REQUEST_REVISION = "request_revision"
    SPAWN_FALLBACK = "spawn_fallback"
    SKIP = "skip"
    NO_CLAIM = "no_claim"
    ERROR = "error"
    TIMEOUT = "timeout"


class LifecycleHookMode(StrEnum):
    """Deployment mode for a lifecycle hook registration."""

    OBSERVE = "observe"
    WARN = "warn"
    ENFORCE = "enforce"
    DISABLED = "disabled"


class LifecycleHookFailurePolicy(StrEnum):
    """How a chain handles hook failures and timeouts."""

    CONTINUE = "continue"
    SKIP_REMAINING = "skip_remaining"
    BLOCK_IF_ENFORCE = "block_if_enforce"
    FAIL_RUN = "fail_run"


class LifecycleHookAuditStatus(StrEnum):
    """Audit row state around hook execution."""

    STARTED = "started"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    NO_CLAIM = "no_claim"


class LifecycleHookEvent(ContractModel):
    """JSON-safe hook input envelope."""

    event_id: str
    event_type: LifecycleHookEventType
    run_id: str | None = None
    attempt_id: str | None = None
    session_id: str | None = None
    seq: int = 1
    source_component: str = "runtime"
    subject_summary: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    durability_level: str = "process_local"

    @field_validator("event_id", "source_component", "durability_level")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("lifecycle hook text fields must be non-empty")
        return value

    @field_validator("seq")
    @classmethod
    def validate_seq(cls, value: int) -> int:
        return ensure_positive_int(value, field_name="seq") or value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_redacted_json(value, field_name="lifecycle hook event metadata")
        return value


class LifecycleHookResult(ContractModel):
    """Result envelope emitted by one lifecycle hook."""

    hook_id: str
    verdict: LifecycleHookVerdict = LifecycleHookVerdict.OBSERVE
    transformed_value_summary: str | None = None
    warning_metadata: dict[str, Any] = Field(default_factory=dict)
    control_metadata: dict[str, Any] = Field(default_factory=dict)
    action_metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: float | None = None
    timed_out: bool = False
    error_class: str | None = None
    redaction_state: str = "redacted"
    prevent_continuation: bool = False
    continuation_behavior: str = "continue"

    @field_validator("hook_id")
    @classmethod
    def validate_hook_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("hook_id must be non-empty")
        return value

    @field_validator("elapsed_ms")
    @classmethod
    def validate_elapsed(cls, value: float | None) -> float | None:
        return ensure_non_negative_float(value, field_name="elapsed_ms")

    @field_validator("warning_metadata", "control_metadata", "action_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_redacted_json(value, field_name="lifecycle hook result metadata")
        return value

    @model_validator(mode="after")
    def validate_timeout_consistency(self) -> "LifecycleHookResult":
        if self.timed_out and self.verdict not in {
            LifecycleHookVerdict.TIMEOUT,
            LifecycleHookVerdict.BLOCK,
        }:
            raise ValueError("timed_out results must use timeout or block verdict")
        if (
            self.verdict
            in {
                LifecycleHookVerdict.ERROR,
                LifecycleHookVerdict.TIMEOUT,
            }
            and not self.error_class
        ):
            raise ValueError("error and timeout hook results must include error_class")
        return self


class LifecycleHookRegistration(ContractModel):
    """Stable hook manifest used for deterministic ordering and reports."""

    hook_id: str
    owner: str
    event_subscriptions: list[LifecycleHookEventType]
    order: int = 100
    timeout_seconds: float | None = None
    mode: LifecycleHookMode = LifecycleHookMode.OBSERVE
    side_effect_permissions: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    compatibility_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("hook_id", "owner")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("registration text fields must be non-empty")
        return value

    @field_validator("order")
    @classmethod
    def validate_order(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="order") or value

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        return ensure_positive_float(value, field_name="timeout_seconds")

    @field_validator("compatibility_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_redacted_json(value, field_name="lifecycle registration metadata")
        return value

    @model_validator(mode="after")
    def validate_subscriptions(self) -> "LifecycleHookRegistration":
        if not self.event_subscriptions:
            raise ValueError("event_subscriptions must not be empty")
        if len(self.event_subscriptions) != len(set(self.event_subscriptions)):
            raise ValueError("event_subscriptions must be unique")
        if self.mode == LifecycleHookMode.ENFORCE and self.side_effect_permissions:
            if "declared" not in self.compatibility_metadata:
                raise ValueError(
                    "enforce hooks with side effects must declare compatibility metadata"
                )
        return self


class LifecycleMiddlewareChain(ContractModel):
    """Ordered lifecycle middleware chain configuration."""

    chain_id: str
    registration_ids: list[str] = Field(default_factory=list)
    failure_policy: LifecycleHookFailurePolicy = LifecycleHookFailurePolicy.CONTINUE
    timeout_default_seconds: float | None = None
    max_hook_count: int = 32
    redaction_policy: dict[str, Any] = Field(
        default_factory=lambda: {"safe_by_default": True}
    )

    @field_validator("chain_id")
    @classmethod
    def validate_chain_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("chain_id must be non-empty")
        return value

    @field_validator("timeout_default_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        return ensure_positive_float(value, field_name="timeout_default_seconds")

    @field_validator("max_hook_count")
    @classmethod
    def validate_max_hook_count(cls, value: int) -> int:
        return ensure_positive_int(value, field_name="max_hook_count") or value

    @field_validator("redaction_policy")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_redacted_json(value, field_name="lifecycle redaction policy")
        return value

    @model_validator(mode="after")
    def validate_chain(self) -> "LifecycleMiddlewareChain":
        if len(self.registration_ids) != len(set(self.registration_ids)):
            raise ValueError("registration_ids must be unique")
        if len(self.registration_ids) > self.max_hook_count:
            raise ValueError("registration_ids exceeds max_hook_count")
        return self


class LifecycleHookAuditRecord(ContractModel):
    """Support-bundle/evidence row linking a hook event and result."""

    audit_id: str
    event: LifecycleHookEvent
    result: LifecycleHookResult
    status: LifecycleHookAuditStatus
    runtime_decision_ref: str | None = None
    adapter_event_ref: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None
    no_claim_reason: str | None = None
    created_at: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("audit_id")
    @classmethod
    def validate_audit_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("audit_id must be non-empty")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_redacted_json(value, field_name="lifecycle audit metadata")
        return value

    @model_validator(mode="after")
    def validate_status_reasons(self) -> "LifecycleHookAuditRecord":
        if (
            self.status == LifecycleHookAuditStatus.NO_CLAIM
            and not self.no_claim_reason
        ):
            raise ValueError("no_claim audit records must include no_claim_reason")
        if self.status == LifecycleHookAuditStatus.SKIPPED and not self.skipped_reason:
            raise ValueError("skipped audit records must include skipped_reason")
        return self


class LifecycleHookCompatibilityReport(ContractModel):
    """Deterministic report for host/product lifecycle hook adoption."""

    report_id: str
    generated_at: str
    product_family: str
    protocol: str = "lifecycle_hooks"
    supported_events: dict[str, str] = Field(default_factory=dict)
    registrations: list[LifecycleHookRegistration] = Field(default_factory=list)
    modes_active: dict[str, str] = Field(default_factory=dict)
    audit_record_count: int = 0
    adapter_event_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    no_claim_events: dict[str, str] = Field(default_factory=dict)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("report_id", "generated_at", "product_family", "protocol")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("compatibility report text fields must be non-empty")
        return value

    @field_validator("supported_events")
    @classmethod
    def validate_supported_events(cls, value: dict[str, str]) -> dict[str, str]:
        for event_name, status in value.items():
            LifecycleHookEventType(event_name)
            if status not in _COMPAT_STATUSES:
                raise ValueError(f"unsupported lifecycle event status: {status}")
        return value

    @field_validator("modes_active")
    @classmethod
    def validate_modes(cls, value: dict[str, str]) -> dict[str, str]:
        for hook_id, mode in value.items():
            if not hook_id.strip():
                raise ValueError("modes_active hook ids must be non-empty")
            LifecycleHookMode(mode)
        return value

    @field_validator("audit_record_count")
    @classmethod
    def validate_count(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="audit_record_count") or value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_redacted_json(value, field_name="lifecycle report metadata")
        return value


def _validate_redacted_json(value: Any, *, field_name: str) -> None:
    ensure_json_serializable(value, field_name=field_name)
    _assert_no_secret_fields(value, path=field_name)


def _assert_no_secret_fields(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}"
            if _is_sensitive_key(key_text):
                if not (isinstance(item, str) and _looks_like_env_name(item)):
                    raise ValueError(
                        "lifecycle hook metadata may name secret env vars but "
                        f"must not contain secret values: {next_path}"
                    )
            _assert_no_secret_fields(item, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_secret_fields(item, path=f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        raise ValueError(
            "lifecycle hook metadata must not contain secret-shaped values: " f"{path}"
        )


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(marker in lower for marker in _SECRET_KEY_MARKERS)


def _looks_like_env_name(value: str) -> bool:
    return bool(value) and value.upper() == value and " " not in value


__all__ = [
    "LifecycleHookAuditRecord",
    "LifecycleHookAuditStatus",
    "LifecycleHookCompatibilityReport",
    "LifecycleHookEvent",
    "LifecycleHookEventType",
    "LifecycleHookFailurePolicy",
    "LifecycleHookMode",
    "LifecycleHookRegistration",
    "LifecycleHookResult",
    "LifecycleHookVerdict",
    "LifecycleMiddlewareChain",
]
