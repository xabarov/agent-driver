"""Shared runtime event emission helpers for step modules."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


def emit_step_event(
    host: Any,
    context: RunContext,
    *,
    event_type: RuntimeEventType,
    payload: dict[str, object] | None = None,
) -> None:
    """Emit a runtime event for the current run/attempt."""
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=event_type,
            payload=payload,
        )
    )


__all__ = ["emit_step_event"]
