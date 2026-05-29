"""Artifact/context-store contracts for preview and pointer split."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import ArtifactKind, SensitivityLevel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class ContextArtifactRef(ContractModel):
    """Reference to an artifact persisted by context-engineering stores."""

    artifact_id: str
    kind: ArtifactKind
    uri: str | None = None
    size_bytes: int | None = None
    sensitivity: SensitivityLevel = SensitivityLevel.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("size_bytes")
    @classmethod
    def validate_size_bytes(cls, value: int | None) -> int | None:
        """Validate non-negative artifact size."""
        return ensure_non_negative_int(value, field_name="size_bytes")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure artifact ref metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="context artifact metadata")


class ArtifactPreview(ContractModel):
    """Bounded preview used in model-facing context window."""

    text: str
    truncated: bool = False
    original_size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("original_size_bytes")
    @classmethod
    def validate_size_bytes(cls, value: int | None) -> int | None:
        """Validate non-negative preview source size."""
        return ensure_non_negative_int(value, field_name="original_size_bytes")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure preview metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="artifact preview metadata")


class StoredArtifact(ContractModel):
    """Stored artifact row with pointer + optional preview."""

    ref: ContextArtifactRef
    content: str
    preview: ArtifactPreview | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure stored artifact metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="stored artifact metadata")
