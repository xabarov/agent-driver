"""Transport-neutral event model for the headless gateway.

A :class:`GatewayEvent` is the unit a headless transport (SSE/HTTP, a platform
adapter, a test harness) consumes. The gateway normalizes the SDK's run output
— including the approval pause — into a small, stable vocabulary so a client
never has to hold Python run objects: it submits text, reads events, and posts
a decision when it sees an ``action_required`` event.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import StrEnum


class GatewayEventKind(StrEnum):
    """Kinds of event the gateway emits over a turn's lifecycle."""

    STARTED = "started"
    ACTION_REQUIRED = "action_required"
    COMPLETED = "completed"
    FAILED = "failed"


class GatewayEvent(ContractModel):
    """One normalized gateway event."""

    kind: GatewayEventKind
    session_id: str
    run_id: str
    seq: int = 0
    data: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """Render as a Server-Sent-Events frame.

        ``id`` carries ``seq`` so a client can resume with ``Last-Event-ID``;
        ``event`` is the kind; ``data`` is the JSON payload.
        """
        payload = json.dumps(
            {
                "session_id": self.session_id,
                "run_id": self.run_id,
                "seq": self.seq,
                **self.data,
            }
        )
        return f"id: {self.seq}\nevent: {self.kind.value}\ndata: {payload}\n\n"


__all__ = ["GatewayEvent", "GatewayEventKind"]
