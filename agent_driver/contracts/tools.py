"""Tool policy and trace contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.artifacts import ArtifactRef
from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import (
    ApprovalMode,
    SideEffectClass,
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
