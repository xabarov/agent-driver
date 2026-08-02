"""Checkpoint reference contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import ensure_json_serializable


class CheckpointRef(ContractModel):
    """Reference pointer to persisted checkpoint state."""

    checkpoint_id: str
    run_id: str
    attempt_id: str
    thread_id: str | None = None
    branch_id: str | None = None
    parent_checkpoint_id: str | None = None
    graph_id: str
    node_id: str | None = None
    created_at: str
    state_version: str
    # F3 — monotonic per-run checkpoint revision (0 for the first, +1 per save
    # along the parent chain). ``state_version`` is the serialization format;
    # ``revision`` is the position of this checkpoint in the run's chain, giving
    # a deterministic ordering signal and a token for revision-based optimistic
    # concurrency (``ResumeCommand.expected_revision``). Additive (default 0).
    revision: int = 0
    storage_backend: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")
