"""Planning/todo state contracts for Phase-6 context engineering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import PlanningTodoStatus
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class TodoState(ContractModel):
    """One planning todo item persisted between turns."""

    todo_id: str
    content: str
    status: PlanningTodoStatus = PlanningTodoStatus.PENDING
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, value: int) -> int:
        """Require non-negative priority."""
        validated = ensure_non_negative_int(value, field_name="priority")
        assert validated is not None
        return validated

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure todo metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="todo metadata")


class PlanningStep(ContractModel):
    """One optional planning step projection for model guidance."""

    step_id: str
    facts_given: list[str] = Field(default_factory=list)
    facts_learned: list[str] = Field(default_factory=list)
    facts_to_lookup: list[str] = Field(default_factory=list)
    facts_to_derive: list[str] = Field(default_factory=list)
    next_plan: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure planning-step metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="planning step metadata")


class PlanningState(ContractModel):
    """Persisted planning state for one run/session."""

    run_id: str
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    todos: list[TodoState] = Field(default_factory=list)
    latest_step: PlanningStep | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure planning state metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="planning state metadata")
