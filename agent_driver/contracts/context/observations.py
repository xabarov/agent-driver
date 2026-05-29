"""Observation memory contracts for bounded model-facing previews."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import ObservationSource, ObservationTrust
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class ObservationProvenance(ContractModel):
    """Provenance metadata for one observation preview."""

    source: ObservationSource
    trust: ObservationTrust
    tool_name: str | None = None
    tool_call_id: str | None = None
    event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure provenance metadata is JSON-compatible."""
        return ensure_json_serializable(
            value, field_name="observation provenance metadata"
        )


class ObservationMemory(ContractModel):
    """Bounded observation preview row for model context."""

    observation_id: str
    text_preview: str
    truncated: bool = False
    original_length: int | None = None
    provenance: ObservationProvenance
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("original_length")
    @classmethod
    def validate_original_length(cls, value: int | None) -> int | None:
        """Validate non-negative original preview length."""
        return ensure_non_negative_int(value, field_name="original_length")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure observation metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="observation metadata")
