"""Memory-step projection contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import MemoryProjectionView, MemoryStepKind
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class MemoryStep(ContractModel):
    """One projected memory step derived from runtime events."""

    step_index: int
    kind: MemoryStepKind
    title: str | None = None
    content: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("step_index")
    @classmethod
    def validate_step_index(cls, value: int) -> int:
        """Validate non-negative step index."""
        validated = ensure_non_negative_int(value, field_name="step_index")
        assert validated is not None
        return validated

    @field_validator("payload", "metadata")
    @classmethod
    def validate_json_payloads(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure payload fields stay JSON-compatible."""
        return ensure_json_serializable(value, field_name="memory step payload")


class MemoryProjection(ContractModel):
    """Projected memory view for full/succinct/replay consumers."""

    run_id: str
    attempt_id: str
    view: MemoryProjectionView
    steps: list[MemoryStep] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible."""
        return ensure_json_serializable(value, field_name="memory projection metadata")

    @model_validator(mode="after")
    def validate_step_order(self) -> "MemoryProjection":
        """Ensure projected steps stay monotonic by step index."""
        indices = [step.step_index for step in self.steps]
        if indices != sorted(indices):
            raise ValueError("memory projection steps must be sorted by step_index")
        return self
