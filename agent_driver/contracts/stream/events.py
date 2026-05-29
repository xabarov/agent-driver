"""Transport-neutral stream event contracts for runtime consumers."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.validation import ensure_json_serializable


class RunStreamEvent(ContractModel):
    """Normalized stream envelope for SSE/CLI/SDK consumers."""

    schema_version: str = "1.0"
    stream_id: str
    run_id: str
    attempt_id: str
    seq: int
    event: str
    source: str = "runtime_event"
    data: dict[str, Any] = Field(default_factory=dict)
    retry_ms: int | None = None
    runtime_event_id: str | None = None
    created_at: str | None = None

    @classmethod
    def from_runtime_event(cls, runtime_event: RuntimeEvent) -> "RunStreamEvent":
        """Project one durable runtime event into stream envelope."""
        payload = ensure_json_serializable(
            dict(runtime_event.payload), field_name="stream event data"
        )
        return cls(
            schema_version="1.0",
            stream_id=f"{runtime_event.run_id}:{runtime_event.seq}",
            run_id=runtime_event.run_id,
            attempt_id=runtime_event.attempt_id,
            seq=runtime_event.seq,
            event=runtime_event.type.value,
            source="runtime_event",
            data=payload,
            runtime_event_id=runtime_event.event_id,
            created_at=runtime_event.created_at,
        )


__all__ = ["RunStreamEvent"]
