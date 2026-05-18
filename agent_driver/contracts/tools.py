"""Tool policy, manifest, and trace contracts."""

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
    ToolPolicyMode,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
    ensure_positive_int,
)


class ToolPolicyInput(ContractModel):
    """Per-run tool policy input passed by the calling application."""

    mode: ToolPolicyMode = ToolPolicyMode.ALLOW_TOOLS
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    max_tool_calls: int | None = None
    approval_required_for_risk: ToolRisk | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_tool_calls")
    @classmethod
    def validate_max_tool_calls(cls, value: int | None) -> int | None:
        """Validate positive max tool calls when provided."""
        return ensure_positive_int(value, field_name="max_tool_calls")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class ToolManifest(ContractModel):
    """Model-facing metadata for one registered tool."""

    name: str
    description: str
    risk: ToolRisk = ToolRisk.LOW
    side_effect: SideEffectClass = SideEffectClass.NONE
    approval_mode: ApprovalMode = ApprovalMode.NEVER
    timeout_seconds: float | None = 30.0
    output_char_budget: int | None = 4000
    idempotent: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        """Validate positive timeout when configured."""
        if value is not None and value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value

    @field_validator("output_char_budget")
    @classmethod
    def validate_output_budget(cls, value: int | None) -> int | None:
        """Validate positive output budget when configured."""
        return ensure_positive_int(value, field_name="output_char_budget")

    @field_validator("metadata")
    @classmethod
    def validate_manifest_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure manifest metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="manifest.metadata")


class ToolCall(ContractModel):
    """Planned tool call parsed from LLM output for deterministic execution."""

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("args", "metadata")
    @classmethod
    def validate_json_payloads(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure tool call payloads stay JSON-compatible."""
        return ensure_json_serializable(value, field_name="tool call payload")


class ToolError(ContractModel):
    """Structured tool failure or policy denial payload."""

    code: str
    message: str
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_error_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure tool error metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="tool error metadata")


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


class ToolPolicyOutcome(ContractModel):
    """Policy engine output for one tool call."""

    decision: ToolPolicyDecision
    reason: str
    interrupt_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_outcome_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure policy outcome metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="policy outcome metadata")


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
