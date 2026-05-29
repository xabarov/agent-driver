"""Durable mailbox contracts for parent/subagent coordination."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.control import utc_now_iso
from agent_driver.contracts.enums.base import StrEnum
from agent_driver.contracts.validation import ensure_json_serializable


class SubagentMailboxDirection(StrEnum):
    """Mailbox delivery direction."""

    PARENT_TO_CHILD = "parent_to_child"
    CHILD_TO_PARENT = "child_to_parent"


class SubagentMailboxKind(StrEnum):
    """Mailbox message categories used by background subagents."""

    MESSAGE = "message"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"
    PLAN_APPROVAL_REQUEST = "plan_approval_request"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"
    TASK_NOTIFICATION = "task_notification"


class SubagentMailboxStatus(StrEnum):
    """Durable mailbox item lifecycle."""

    QUEUED = "queued"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    CANCELLED = "cancelled"


class SubagentMailboxItem(ContractModel):
    """One durable parent/subagent coordination message."""

    mailbox_id: str = Field(default_factory=lambda: f"mbox_{uuid4().hex[:12]}")
    parent_run_id: str
    direction: SubagentMailboxDirection
    kind: SubagentMailboxKind
    status: SubagentMailboxStatus = SubagentMailboxStatus.QUEUED
    subagent_run_id: str | None = None
    child_run_id: str | None = None
    group_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "runtime"
    dedupe_key: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    delivered_at: str | None = None
    acknowledged_at: str | None = None
    cancelled_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload", "metadata")
    @classmethod
    def validate_json_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure mailbox payloads remain JSON-compatible."""
        return ensure_json_serializable(value, field_name="subagent mailbox payload")

    @field_validator("parent_run_id", "source")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        """Normalize required routing labels."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("mailbox text field must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def validate_child_route(self) -> "SubagentMailboxItem":
        """Parent-to-child messages require a stable child identifier."""
        if self.direction == SubagentMailboxDirection.PARENT_TO_CHILD and not (
            self.subagent_run_id or self.child_run_id
        ):
            raise ValueError("parent_to_child mailbox item requires child route")
        return self


__all__ = [
    "SubagentMailboxDirection",
    "SubagentMailboxItem",
    "SubagentMailboxKind",
    "SubagentMailboxStatus",
]
