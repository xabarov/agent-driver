"""Tool policy input/output contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import ToolPolicyDecision, ToolPolicyMode, ToolRisk
from agent_driver.contracts.validation import (
    ensure_json_serializable,
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


__all__ = ["ToolPolicyInput", "ToolPolicyOutcome"]
