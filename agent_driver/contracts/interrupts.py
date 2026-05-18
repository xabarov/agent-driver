"""Interrupt and resume contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import InterruptReason, ResumeAction, ToolRisk
from agent_driver.contracts.validation import ensure_json_serializable


class ResumeCommand(ContractModel):
    """Command payload used to continue a paused run."""

    interrupt_id: str
    action: ResumeAction
    message: str | None = None
    edited_tool_args: dict[str, Any] | None = None
    state_patch: dict[str, Any] | None = None
    approved_by: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @field_validator("edited_tool_args", "state_patch")
    @classmethod
    def validate_optional_json_payload(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Validate optional edit/patch payload shape."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="resume payload")

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ResumeCommand":
        """Enforce action-specific payload invariants."""
        if self.action == ResumeAction.EDIT and not (
            self.edited_tool_args or self.state_patch
        ):
            raise ValueError("edit action requires edited_tool_args or state_patch")
        if self.action == ResumeAction.CLARIFY and not (self.message or "").strip():
            raise ValueError("clarify action requires message")
        if (
            self.action in {ResumeAction.APPROVE, ResumeAction.REJECT}
            and self.state_patch
        ):
            raise ValueError("approve/reject actions cannot mutate state_patch")
        return self


class InterruptRequest(ContractModel):
    """Persisted pause request for human review or clarification."""

    interrupt_id: str
    run_id: str
    attempt_id: str
    checkpoint_id: str
    reason: InterruptReason
    title: str
    description: str
    risk: ToolRisk | None = None
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[ResumeAction] = Field(default_factory=list)
    editable_fields: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("proposed_action", "metadata")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure proposed action and metadata are JSON-compatible."""
        return ensure_json_serializable(value, field_name="interrupt payload")

    @model_validator(mode="after")
    def validate_allowed_actions(self) -> "InterruptRequest":
        """Require at least one allowed resume action."""
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        return self
