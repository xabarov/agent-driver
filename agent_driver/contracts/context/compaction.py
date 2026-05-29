"""Compaction contracts for Phase 8 orchestration and audit."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import CompactionMode, CompactionSkipReason
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_non_negative_int,
)


class CompactionDecision(ContractModel):
    """Decision emitted by compaction eligibility/orchestration layer."""

    eligible: bool
    mode: CompactionMode = CompactionMode.NONE
    skip_reason: CompactionSkipReason | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure decision metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="compaction decision metadata")

    @model_validator(mode="after")
    def validate_consistency(self) -> "CompactionDecision":
        """Ensure skip decisions and modes are coherent."""
        if self.eligible and self.mode == CompactionMode.NONE:
            raise ValueError("eligible decision must specify compaction mode")
        if not self.eligible and self.skip_reason is None:
            raise ValueError("ineligible decision must specify skip_reason")
        return self


class CompactionResult(ContractModel):
    """Result of one compaction attempt or execution."""

    compaction_id: str
    mode: CompactionMode
    success: bool
    model: str | None = None
    latency_ms: int | None = None
    input_tokens_estimate: int | None = None
    output_tokens_estimate: int | None = None
    estimated_cost: float | None = None
    retained_digest_ids: list[str] = Field(default_factory=list)
    retained_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("latency_ms", "input_tokens_estimate", "output_tokens_estimate")
    @classmethod
    def validate_non_negative_ints(cls, value: int | None) -> int | None:
        """Require non-negative integer metrics."""
        return ensure_non_negative_int(value, field_name="compaction numeric metric")

    @field_validator("estimated_cost")
    @classmethod
    def validate_non_negative_cost(cls, value: float | None) -> float | None:
        """Require non-negative compaction cost estimate."""
        return ensure_non_negative_float(value, field_name="estimated_cost")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure result metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="compaction result metadata")


class CompactionAudit(ContractModel):
    """Top-level audit envelope recorded in metadata and replay."""

    decision: CompactionDecision
    result: CompactionResult | None = None
    failures: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("failures")
    @classmethod
    def validate_failures(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Ensure failure entries are JSON-compatible."""
        return [
            ensure_json_serializable(item, field_name="compaction failure entry")
            for item in value
        ]

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure audit metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="compaction audit metadata")


__all__ = ["CompactionAudit", "CompactionDecision", "CompactionResult"]
