"""Tool result envelope and trace contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.artifacts import ArtifactRef
from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import (
    ApprovalMode,
    GuardrailDecision,
    SideEffectClass,
    ToolPolicyDecision,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.tools.calls import ToolCall, ToolError
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class ToolResultEnvelope(ContractModel):
    """Normalized result envelope emitted by governed executor."""

    call: ToolCall
    decision: ToolPolicyDecision = ToolPolicyDecision.ALLOW
    guardrail_decision: GuardrailDecision = GuardrailDecision.ALLOW
    summary: str | None = None
    structured_output: dict[str, Any] | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    truncated: bool = False
    error: ToolError | None = None
    interrupt: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("structured_output", "interrupt")
    @classmethod
    def validate_optional_json_payloads(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Ensure optional payloads stay JSON-compatible."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="tool result payload")

    @field_validator("metadata")
    @classmethod
    def validate_result_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure result metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="tool result metadata")

    @model_validator(mode="after")
    def validate_consistency(self) -> "ToolResultEnvelope":
        """Enforce coherent envelope shape for deny/interrupt decisions."""
        if self.decision == ToolPolicyDecision.DENY and self.error is None:
            raise ValueError("deny decision requires error payload")
        if self.decision == ToolPolicyDecision.INTERRUPT and self.interrupt is None:
            raise ValueError("interrupt decision requires interrupt payload")
        return self


class ToolTrace(ContractModel):
    """Canonical trace row for one tool call."""

    step: int
    tool_name: str
    tool_call_id: str | None = None
    status: ToolTraceStatus
    args_summary: dict[str, str] = Field(default_factory=dict)
    result_summary: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    risk: ToolRisk
    side_effect: SideEffectClass
    approval_mode: ApprovalMode
    duration_ms: int | None = None
    error_code: str | None = None
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("step")
    @classmethod
    def validate_step(cls, value: int) -> int:
        """Validate non-negative step index."""
        return int(ensure_non_negative_int(value, field_name="step"))

    @field_validator("duration_ms")
    @classmethod
    def validate_duration(cls, value: int | None) -> int | None:
        """Validate non-negative tool duration."""
        return ensure_non_negative_int(value, field_name="duration_ms")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


__all__ = ["ToolResultEnvelope", "ToolTrace"]
