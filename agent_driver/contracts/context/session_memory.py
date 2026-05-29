"""Semantic session-memory contracts for Phase 8 compaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class SessionMemory(ContractModel):
    """Semantic session memory extracted from prior turns."""

    memory_id: str
    session_id: str
    version: int = 1
    summary: str
    key_facts: list[str] = Field(default_factory=list)
    pending_tasks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    last_summarized_turn_index: int
    source_digest_ids: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("last_summarized_turn_index")
    @classmethod
    def validate_turn_index(cls, value: int) -> int:
        """Require non-negative summarized turn index."""
        validated = ensure_non_negative_int(value, field_name="last_summarized_turn_index")
        assert validated is not None
        return validated

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure session-memory metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="session memory metadata")


__all__ = ["SessionMemory"]
