"""Runtime event contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

from pydantic import Field, field_validator

from agent_driver.contracts.artifacts import RedactionInfo
from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import EventSeverity, RuntimeEventType
from agent_driver.contracts.validation import ensure_json_serializable


class RuntimeEvent(ContractModel):
    """Canonical runtime event used by stream and persistence adapters."""

    event_id: str
    type: RuntimeEventType
    run_id: str
    attempt_id: str
    seq: int
    created_at: str
    checkpoint_id: str | None = None
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    severity: EventSeverity = EventSeverity.INFO
    redaction: RedactionInfo | None = None

    @field_validator("seq")
    @classmethod
    def validate_seq(cls, value: int) -> int:
        """Require positive monotonic sequence number."""
        if value < 1:
            raise ValueError("seq must be >= 1")
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure event payload is JSON-compatible."""
        return ensure_json_serializable(value, field_name="payload")


class RuntimeEventContext(TypedDict):
    """Required identifiers for constructing runtime events."""

    run_id: str
    attempt_id: str
    seq: int


class RuntimeEventOptions(TypedDict, total=False):
    """Optional fields for runtime event construction."""

    payload: dict[str, Any]
    checkpoint_id: str | None
    node_id: str | None
    trace_id: str | None
    severity: EventSeverity


def new_runtime_event(
    *,
    event_type: RuntimeEventType,
    context: RuntimeEventContext,
    options: RuntimeEventOptions | None = None,
) -> RuntimeEvent:
    """Build a runtime event from required context plus optional fields."""
    opts = options or {}
    return RuntimeEvent(
        event_id=f"evt_{uuid4().hex}",
        type=event_type,
        run_id=context["run_id"],
        attempt_id=context["attempt_id"],
        seq=context["seq"],
        created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        checkpoint_id=opts.get("checkpoint_id"),
        node_id=opts.get("node_id"),
        payload=opts.get("payload", {}),
        trace_id=opts.get("trace_id"),
        severity=opts.get("severity", EventSeverity.INFO),
    )
