"""Structured extraction adapter contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError

from agent_driver.contracts.base import ContractModel


class StructuredExtractionFailure(ContractModel):
    """Serializable failure from a structured extraction or validation step."""

    purpose: str
    error_kind: str
    message: str
    attempts: int = 1
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def as_observation(self) -> dict[str, Any]:
        """Return a runtime-observation-friendly payload."""
        return {
            "kind": "structured_extraction_failure",
            "purpose": self.purpose,
            "error_kind": self.error_kind,
            "message": self.message,
            "attempts": self.attempts,
            "validation_errors": self.validation_errors,
            "metadata": self.metadata,
        }


class StructuredExtractionError(ValueError):
    """Raised when structured extraction cannot produce a valid model."""

    def __init__(self, failure: StructuredExtractionFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class StructuredDependencyError(ImportError):
    """Raised when an optional structured extraction dependency is missing."""


def validation_failure(
    *,
    purpose: str,
    error: ValidationError,
    attempts: int = 1,
    metadata: dict[str, Any] | None = None,
) -> StructuredExtractionFailure:
    """Build a JSON-safe failure payload from Pydantic validation errors."""
    errors: list[dict[str, Any]] = []
    for item in error.errors():
        row = dict(item)
        ctx = row.get("ctx")
        if isinstance(ctx, dict):
            row["ctx"] = {str(key): str(value) for key, value in ctx.items()}
        errors.append(row)
    return StructuredExtractionFailure(
        purpose=purpose,
        error_kind="validation_error",
        message=str(error),
        attempts=attempts,
        validation_errors=errors,
        metadata=metadata or {},
    )


def require_instructor():
    """Import Instructor lazily so the default install has no extra dependency."""
    try:
        import instructor  # type: ignore[import-not-found]
    except ImportError as exc:
        raise StructuredDependencyError(
            "Instructor support requires installing agent-driver[instructor]."
        ) from exc
    return instructor


__all__ = [
    "StructuredDependencyError",
    "StructuredExtractionError",
    "StructuredExtractionFailure",
    "require_instructor",
    "validation_failure",
]
