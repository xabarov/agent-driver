"""Steering control-plane contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums.base import StrEnum
from agent_driver.contracts.validation import ensure_json_serializable


def utc_now_iso() -> str:
    """Return a stable UTC timestamp string for control records."""
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


class ControlKind(StrEnum):
    """Transport-neutral steering command kind."""

    INTERRUPT = "interrupt"
    ENQUEUE_USER_MESSAGE = "enqueue_user_message"
    CANCEL_QUEUED_MESSAGE = "cancel_queued_message"
    SET_MODEL = "set_model"
    SET_TOOL_POLICY = "set_tool_policy"
    SET_PERMISSION_MODE = "set_permission_mode"
    SET_MAX_THINKING_TOKENS = "set_max_thinking_tokens"
    PATCH_PLANNING_STATE = "patch_planning_state"
    STOP_SUBAGENT = "stop_subagent"
    CONTINUE_SUBAGENT = "continue_subagent"
    GET_CONTEXT_USAGE = "get_context_usage"


class ControlPriority(StrEnum):
    """Command queue priority."""

    NOW = "now"
    NEXT = "next"
    LATER = "later"


class CommandQueueStatus(StrEnum):
    """Durable command queue lifecycle status."""

    QUEUED = "queued"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ControlRequest(ContractModel):
    """Host request to steer a live or resumable run."""

    kind: ControlKind
    run_id: str | None = None
    thread_id: str | None = None
    agent_id: str | None = None
    priority: ControlPriority = ControlPriority.NEXT
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "host"
    dedupe_key: str | None = None
    control_id: str = Field(default_factory=lambda: f"ctrl_{uuid4().hex[:12]}")
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload", "metadata")
    @classmethod
    def validate_json_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure control payloads remain JSON-compatible."""
        return ensure_json_serializable(value, field_name="control payload")

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        """Normalize source label."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def validate_routing(self) -> "ControlRequest":
        """Require at least one stable routing identifier."""
        if not (self.run_id or self.thread_id or self.agent_id):
            raise ValueError("control request requires run_id, thread_id, or agent_id")
        return self


class ControlResponse(ContractModel):
    """Result of accepting or applying a control request."""

    ok: bool
    control_id: str | None = None
    queue_id: str | None = None
    error: str | None = None
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pending_approvals", "metadata")
    @classmethod
    def validate_response_payload(cls, value: Any) -> Any:
        """Ensure response payload fields are JSON-compatible."""
        return ensure_json_serializable(value, field_name="control response payload")


class CommandQueueItem(ContractModel):
    """Durable queued steering command."""

    queue_id: str
    control_id: str
    kind: ControlKind
    priority: ControlPriority
    status: CommandQueueStatus = CommandQueueStatus.QUEUED
    run_id: str | None = None
    thread_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "host"
    dedupe_key: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    applied_at: str | None = None
    cancelled_at: str | None = None
    failed_at: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_request(cls, request: ControlRequest) -> "CommandQueueItem":
        """Create a queued item from a host control request."""
        now = utc_now_iso()
        return cls(
            queue_id=f"cmd_{uuid4().hex[:12]}",
            control_id=request.control_id,
            kind=request.kind,
            priority=request.priority,
            run_id=request.run_id,
            thread_id=request.thread_id,
            agent_id=request.agent_id,
            payload=dict(request.payload),
            source=request.source,
            dedupe_key=request.dedupe_key,
            created_at=now,
            updated_at=now,
            metadata=dict(request.metadata),
        )

    @field_validator("payload", "metadata")
    @classmethod
    def validate_item_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure queued item payloads remain JSON-compatible."""
        return ensure_json_serializable(value, field_name="command queue payload")


__all__ = [
    "CommandQueueItem",
    "CommandQueueStatus",
    "ControlKind",
    "ControlPriority",
    "ControlRequest",
    "ControlResponse",
    "utc_now_iso",
]
