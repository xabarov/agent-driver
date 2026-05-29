"""CodeAgent contracts for sandboxed action execution."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class CodeAgentLimits(ContractModel):
    """Execution safety limits for one code action."""

    max_operations: int = 800
    max_loops: int = 40
    max_exec_ms: int = 2_000
    max_output_chars: int = 400

    @field_validator("max_operations", "max_loops", "max_exec_ms", "max_output_chars")
    @classmethod
    def validate_limits(cls, value: int) -> int:
        """Require non-negative integer limits."""
        validated = ensure_non_negative_int(value, field_name="code-agent limit")
        assert validated is not None
        return validated


class CodeAgentAction(ContractModel):
    """One executable code action parsed from model output."""

    action_id: str
    code: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure action metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="code action metadata")


class CodeAgentObservation(ContractModel):
    """Captured stdout/stderr observation payload."""

    source: str
    text_preview: str
    truncated: bool = False
    artifact_ref: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_ref", "metadata")
    @classmethod
    def validate_json_fields(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Ensure observation payload fields are JSON-compatible."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="code observation payload")


class CodeAgentFinalAnswer(ContractModel):
    """Final answer extracted from action execution."""

    text: str
    source: str = "helper"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure final answer metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="code final answer metadata")


class CodeAgentExecutionResult(ContractModel):
    """Structured result of one code action execution."""

    final_answer: CodeAgentFinalAnswer | None = None
    observations: list[CodeAgentObservation] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_results", "metadata")
    @classmethod
    def validate_json_fields(cls, value: Any) -> Any:
        """Ensure execution payload fields stay JSON-compatible."""
        return ensure_json_serializable(value, field_name="code execution payload")


__all__ = [
    "CodeAgentAction",
    "CodeAgentExecutionResult",
    "CodeAgentFinalAnswer",
    "CodeAgentLimits",
    "CodeAgentObservation",
]
