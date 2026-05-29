"""Artifact, trace, warning and sensitivity contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import (
    ArtifactKind,
    SensitivityLevel,
    WarningSeverity,
    WarningSource,
)
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class ArtifactRef(ContractModel):
    """Reference to an offloaded artifact."""

    artifact_id: str
    kind: ArtifactKind
    uri: str | None = None
    title: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    preview: str | None = None
    sensitivity: SensitivityLevel = SensitivityLevel.UNKNOWN
    created_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("size_bytes")
    @classmethod
    def validate_size(cls, value: int | None) -> int | None:
        """Validate non-negative artifact size."""
        return ensure_non_negative_int(value, field_name="size_bytes")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class TraceRef(ContractModel):
    """Cross-system trace identifiers attached to run output."""

    trace_id: str | None = None
    span_id: str | None = None
    phoenix_trace_id: str | None = None
    langfuse_trace_id: str | None = None
    langsmith_trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class RunWarning(ContractModel):
    """Structured warning emitted by runtime or policy layers."""

    code: str
    message: str
    severity: WarningSeverity
    source: WarningSource
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")


class RedactionInfo(ContractModel):
    """Describes redaction policy and affected fields."""

    applied: bool
    policy: str | None = None
    redacted_fields: list[str] = Field(default_factory=list)
    sensitivity: SensitivityLevel = SensitivityLevel.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")
