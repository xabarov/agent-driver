"""Planning/todo state contracts for Phase-6 context engineering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import (
    PlanningHintLevel,
    PlanningModeState,
    PlanningPolicyMode,
    PlanningTodoStatus,
)
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class TodoState(ContractModel):
    """One planning todo item persisted between turns."""

    todo_id: str
    content: str
    status: PlanningTodoStatus = PlanningTodoStatus.PENDING
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: int) -> int:
        """Require non-negative priority."""
        validated = ensure_non_negative_int(value, field_name="priority")
        assert validated is not None
        return validated

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure todo metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="todo metadata")


class PlanningStep(ContractModel):
    """One optional planning step projection for model guidance."""

    step_id: str
    facts_given: list[str] = Field(default_factory=list)
    facts_learned: list[str] = Field(default_factory=list)
    facts_to_lookup: list[str] = Field(default_factory=list)
    facts_to_derive: list[str] = Field(default_factory=list)
    next_plan: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure planning-step metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="planning step metadata")


class PlanningState(ContractModel):
    """Persisted planning state for one run/session."""

    run_id: str
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    todos: list[TodoState] = Field(default_factory=list)
    latest_step: PlanningStep | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure planning state metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="planning state metadata")


class PlanArtifact(ContractModel):
    """Durable approval artifact for force-planning workflows.

    This is intentionally separate from ``PlanningState``. The todo list is a
    live progress surface, while a plan artifact is the reviewed document that
    can gate side-effecting execution.
    """

    plan_id: str
    run_id: str
    thread_id: str | None = None
    agent_id: str
    path: str | None = None
    content: str
    content_hash: str
    status: PlanningModeState = PlanningModeState.COLLECTING
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    approved_at: str | None = None
    approved_by: str | None = None
    rejected_at: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_artifact_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure plan artifact metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="plan artifact metadata")

    @model_validator(mode="after")
    def validate_status_metadata(self) -> "PlanArtifact":
        """Keep approval/rejection metadata aligned with status."""
        if self.status == PlanningModeState.APPROVED and not self.approved_at:
            raise ValueError("approved plan artifacts require approved_at")
        if self.status == PlanningModeState.REJECTED and not self.rejected_at:
            raise ValueError("rejected plan artifacts require rejected_at")
        return self


class PlanApprovalPayload(ContractModel):
    """UI/runtime payload shown when a plan waits for human approval."""

    plan_id: str
    run_id: str
    agent_id: str
    content: str
    content_hash: str
    path: str | None = None
    title: str = "Approve plan?"
    description: str = "Review the proposed plan before execution continues."
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_payload_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure plan approval metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="plan approval metadata")


class PlanningHint(ContractModel):
    """Deterministic hint for adaptive plan-mode behavior."""

    level: PlanningHintLevel = PlanningHintLevel.NONE
    reason: str = "planning not needed"
    signals: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("signals")
    @classmethod
    def validate_signals(cls, value: list[str]) -> list[str]:
        """Keep signals compact and non-empty."""
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("metadata")
    @classmethod
    def validate_hint_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure planning hint metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="planning hint metadata")


class PlanningPolicyInput(ContractModel):
    """Typed force-planning policy embedded in tool-policy metadata."""

    enabled: bool | None = None
    mode: PlanningPolicyMode = PlanningPolicyMode.REQUIRED_FOR_WRITES
    approved: bool = False
    approved_plan_id: str | None = None
    approved_plan: dict[str, Any] | None = None
    exempt_tools: list[str] | None = None
    gated_tools: list[str] | None = None
    gated_side_effects: list[str] | None = None
    min_risk: str | None = None
    multistep: bool = False
    expected_steps: int | None = None
    step_threshold: int = 2
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "PlanningPolicyInput | None":
        """Build policy from legacy/current ``ToolPolicyInput.metadata``."""
        raw = metadata.get("force_planning")
        if isinstance(raw, dict):
            payload = dict(raw)
            if "mode" in payload and "enabled" not in payload:
                payload["enabled"] = True
            return cls.model_validate(payload)
        if metadata.get("force_planning_enabled") is True:
            return cls(enabled=True)
        if metadata.get("planning_hint_enforce") is True:
            hint = metadata.get("planning_hint")
            if isinstance(hint, dict) and hint.get("level") == "required":
                signals = hint.get("signals")
                signal_rows = signals if isinstance(signals, list) else []
                signal_set = {str(item) for item in signal_rows if str(item).strip()}
                if "subagent_spawn_requested" in signal_set:
                    return cls(enabled=True, gated_tools=["agent_tool"])
                if "expected_steps_ge_4" in signal_set:
                    return cls(
                        enabled=True,
                        mode=PlanningPolicyMode.ALWAYS_FOR_MULTISTEP,
                        expected_steps=4,
                    )
                return cls(enabled=True)
        return None

    @field_validator(
        "exempt_tools",
        "gated_tools",
        "gated_side_effects",
    )
    @classmethod
    def validate_optional_string_list(
        cls, value: list[str] | None
    ) -> list[str] | None:
        """Normalize optional string lists and drop blank entries."""
        if value is None:
            return None
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("expected_steps")
    @classmethod
    def validate_expected_steps(cls, value: int | None) -> int | None:
        """Require non-negative expected step count when present."""
        return ensure_non_negative_int(value, field_name="expected_steps")

    @field_validator("step_threshold")
    @classmethod
    def validate_step_threshold(cls, value: int) -> int:
        """Require a positive multistep threshold."""
        if isinstance(value, bool) or value <= 0:
            raise ValueError("step_threshold must be a positive integer")
        return value

    @field_validator("approved_plan", "metadata")
    @classmethod
    def validate_policy_metadata(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Ensure nested policy metadata is JSON-compatible."""
        if value is None:
            return None
        return ensure_json_serializable(value, field_name="planning policy metadata")
