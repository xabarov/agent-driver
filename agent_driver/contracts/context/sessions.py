"""Session and turn digest contracts for context engineering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class SessionRef(ContractModel):
    """Canonical session identity and metadata."""

    session_id: str
    run_id: str
    attempt_id: str
    workspace_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure session metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="session metadata")


class TurnDigest(ContractModel):
    """Compact digest for one turn to support bounded context windows."""

    digest_id: str
    turn_index: int
    summary: str
    references: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("turn_index")
    @classmethod
    def validate_turn_index(cls, value: int) -> int:
        """Require non-negative turn index."""
        validated = ensure_non_negative_int(value, field_name="turn_index")
        assert validated is not None
        return validated

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure digest metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="turn digest metadata")


class SessionTurn(ContractModel):
    """One persisted session turn with optional digest reference."""

    session_id: str
    turn_index: int
    message: ChatMessage
    digest: TurnDigest | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("turn_index")
    @classmethod
    def validate_turn_index(cls, value: int) -> int:
        """Require non-negative turn index."""
        validated = ensure_non_negative_int(value, field_name="turn_index")
        assert validated is not None
        return validated

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure turn metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="session turn metadata")
