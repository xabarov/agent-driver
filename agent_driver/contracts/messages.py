"""Message contracts for runtime input and output."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.validation import ensure_json_serializable


class ChatMessage(ContractModel):
    """Minimal engine-neutral chat message."""

    role: ChatRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(metadata, field_name="metadata")
