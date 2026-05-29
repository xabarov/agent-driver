"""Deterministic context trimming contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import TrimAction
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class ContextBudget(ContractModel):
    """Budget constraints used by deterministic context trimming."""

    max_chars: int
    max_messages: int | None = None
    max_observations: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_chars", "max_messages", "max_observations")
    @classmethod
    def validate_limits(cls, value: int | None) -> int | None:
        """Require non-negative limits."""
        return ensure_non_negative_int(value, field_name="context budget limit")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure budget metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="context budget metadata")


class TrimAuditRecord(ContractModel):
    """One trimming action record for traceable deterministic behavior."""

    record_id: str
    kind: str
    action: TrimAction
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure trim audit metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="trim audit metadata")


class TrimmedContext(ContractModel):
    """Trimmed context payload returned to runtime/prompt assembly."""

    prompt_messages: list[dict[str, Any]] = Field(default_factory=list)
    retained_artifact_ids: list[str] = Field(default_factory=list)
    retained_digest_ids: list[str] = Field(default_factory=list)
    audit: list[TrimAuditRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt_messages")
    @classmethod
    def validate_prompt_messages(
        cls, value: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Ensure trimmed prompt messages are JSON-compatible."""
        return [
            ensure_json_serializable(item, field_name="trimmed prompt message")
            for item in value
        ]

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure trimmed context metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="trimmed context metadata")
