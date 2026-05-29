"""Tool call and structured error contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_json_serializable


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


__all__ = ["ToolCall", "ToolError"]
