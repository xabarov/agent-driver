"""Trace-safe harness policy contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_json_serializable

_VALID_POLICY_MODES = frozenset({"observe", "warn", "enforce", "fail_closed"})
_VALID_SIGNAL_CLASSES = frozenset(
    {
        "provider_preflight",
        "provider_error",
        "runtime_decision",
        "tool_guardrail",
        "provenance_contract",
        "lifecycle",
        "goal_state",
        "usage_budget",
        "user_control",
        "host_metadata",
    }
)
_VALID_POLICY_ACTIONS = frozenset(
    {
        "continue",
        "warn",
        "retry",
        "compact",
        "switch_provider_route",
        "reshape_request",
        "force_final",
        "ask_user",
        "interrupt_for_approval",
        "block_tool",
        "rollback",
        "abort",
        "mark_achieved",
        "mark_blocked",
        "fail_fast",
    }
)
_VALID_EVALUATION_STATUSES = frozenset({"matched", "not_matched", "skipped"})
_VALID_HEARTBEAT_STATUSES = frozenset({"unknown", "active", "stale", "terminal"})
_VALID_VALIDATION_GATE_STATUSES = frozenset(
    {"passed", "failed", "skipped", "blocked", "not_run"}
)


class HarnessPolicyProfile(ContractModel):
    """Host-selected policy bundle for deterministic observe/warn/enforce rollout."""

    profile_id: str = "default-observe"
    mode: str = "observe"
    enabled_policy_ids: list[str] = Field(default_factory=list)
    budgets: dict[str, Any] = Field(default_factory=dict)
    provider_route_preferences: dict[str, Any] = Field(default_factory=dict)
    required_evidence: list[str] = Field(default_factory=list)
    side_effect_rules: dict[str, Any] = Field(default_factory=dict)
    rollout_tags: list[str] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in _VALID_POLICY_MODES:
            raise ValueError(f"unsupported harness policy mode: {value}")
        return value

    @field_validator("budgets", "provider_route_preferences", "side_effect_rules")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="policy profile metadata")


class PolicySignal(ContractModel):
    """Normalized policy input derived from existing diagnostics."""

    signal_id: str
    signal_class: str
    reason: str
    severity: str = "warning"
    source: str = "runtime"
    run_id: str | None = None
    attempt_id: str | None = None
    seq: int | None = None
    affected_tools: list[str] = Field(default_factory=list)
    affected_artifacts: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    observed_evidence: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("signal_class")
    @classmethod
    def validate_signal_class(cls, value: str) -> str:
        if value not in _VALID_SIGNAL_CLASSES:
            raise ValueError(f"unsupported policy signal class: {value}")
        return value

    @field_validator("seq")
    @classmethod
    def validate_seq(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("seq must be >= 1")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="policy signal metadata")


class PolicyAction(ContractModel):
    """Action selected by a policy evaluation before any runtime enforcement."""

    action: str
    reason: str
    affected_tools: list[str] = Field(default_factory=list)
    affected_artifacts: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    retry_budget: int | None = None
    rollback_available: bool | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in _VALID_POLICY_ACTIONS:
            raise ValueError(f"unsupported policy action: {value}")
        return value

    @field_validator("retry_budget")
    @classmethod
    def validate_retry_budget(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("retry_budget must be >= 0")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="policy action metadata")


class PolicyEvaluation(ContractModel):
    """Deterministic result for one policy pass."""

    evaluation_id: str
    policy_id: str
    profile_id: str
    mode: str
    status: str
    matched_signal_ids: list[str] = Field(default_factory=list)
    selected_action: str = "continue"
    confidence: float = 1.0
    reason: str = "no_policy_match"
    affected_tools: list[str] = Field(default_factory=list)
    affected_artifacts: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    observed_evidence: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    enforcement_skipped_reason: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in _VALID_POLICY_MODES:
            raise ValueError(f"unsupported harness policy mode: {value}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _VALID_EVALUATION_STATUSES:
            raise ValueError(f"unsupported policy evaluation status: {value}")
        return value

    @field_validator("selected_action")
    @classmethod
    def validate_selected_action(cls, value: str) -> str:
        if value not in _VALID_POLICY_ACTIONS:
            raise ValueError(f"unsupported policy action: {value}")
        return value

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("budget", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="policy evaluation metadata")


class RunSupervisorState(ContractModel):
    """Replayable per-run supervision state derived from events and policies."""

    run_id: str | None = None
    session_id: str | None = None
    lifecycle_state: str = "unknown"
    heartbeat_status: str = "unknown"
    heartbeat_seq: int | None = None
    current_goal_id: str | None = None
    active_policy_mode: str = "observe"
    pending_controls: list[dict[str, Any]] = Field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    retry_counters: dict[str, int] = Field(default_factory=dict)
    fallback_counters: dict[str, int] = Field(default_factory=dict)
    reconnect_cursor: str | None = None
    terminal_verdict: str | None = None
    recoverable: bool = False
    orphaned: bool = False
    policy_evaluation_count: int = 0
    policy_would_fire_ids: list[str] = Field(default_factory=list)
    last_policy_actions: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("heartbeat_status")
    @classmethod
    def validate_heartbeat_status(cls, value: str) -> str:
        if value not in _VALID_HEARTBEAT_STATUSES:
            raise ValueError(f"unsupported heartbeat status: {value}")
        return value

    @field_validator("heartbeat_seq")
    @classmethod
    def validate_heartbeat_seq(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("heartbeat_seq must be >= 1")
        return value

    @field_validator(
        "pending_controls",
        "pending_approvals",
        "retry_counters",
        "fallback_counters",
        "redacted_metadata",
    )
    @classmethod
    def validate_json_fields(cls, value: Any) -> Any:
        return ensure_json_serializable(value, field_name="supervisor state metadata")


class ValidationGateResult(ContractModel):
    """Reusable validation evidence record for policy/supervision gates."""

    gate_id: str
    status: str = "not_run"
    evidence_path: str | None = None
    command: str | None = None
    reason: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _VALID_VALIDATION_GATE_STATUSES:
            raise ValueError(f"unsupported validation gate status: {value}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="validation gate metadata")


__all__ = [
    "HarnessPolicyProfile",
    "PolicyAction",
    "PolicyEvaluation",
    "PolicySignal",
    "RunSupervisorState",
    "ValidationGateResult",
]
