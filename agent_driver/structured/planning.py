"""Structured plan artifact validation prototype."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.structured.contracts import (
    StructuredExtractionError,
    validation_failure,
)


class StructuredPlanStep(ContractModel):
    """One validated step in an approval-oriented plan draft."""

    title: str
    action: str
    verification: str | None = None

    @field_validator("title", "action")
    @classmethod
    def require_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("plan step fields must be non-empty")
        return cleaned


class StructuredPlanArtifactDraft(ContractModel):
    """Schema for plan content before it becomes an approval artifact."""

    scope: str
    steps: list[StructuredPlanStep] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    rollback: str | None = None
    requested_permissions: list[str] = Field(default_factory=list)

    @field_validator("scope")
    @classmethod
    def require_scope(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("plan scope must be non-empty")
        return cleaned

    @field_validator("risks", "verification", "requested_permissions")
    @classmethod
    def compact_string_lists(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]


def validate_plan_artifact_payload(
    payload: dict[str, Any],
    *,
    purpose: str = "plan_artifact_validation",
) -> StructuredPlanArtifactDraft:
    """Validate a proposed plan artifact draft or raise structured failure."""
    try:
        return StructuredPlanArtifactDraft.model_validate(payload)
    except ValidationError as exc:
        raise StructuredExtractionError(
            validation_failure(purpose=purpose, error=exc)
        ) from exc


__all__ = [
    "StructuredPlanArtifactDraft",
    "StructuredPlanStep",
    "validate_plan_artifact_payload",
]
