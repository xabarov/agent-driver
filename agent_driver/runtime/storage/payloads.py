"""Payload mappers for checkpoint and event stores."""

from __future__ import annotations

from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage.protocols import CheckpointRecord


def checkpoint_record_from_state(state: RuntimeState) -> CheckpointRecord | None:
    """Build checkpoint row from runtime state when checkpoint ref is present."""
    if state.checkpoint is None:
        return None
    return CheckpointRecord(ref=state.checkpoint, state=state)


def checkpoint_record_from_json(payload: str) -> CheckpointRecord | None:
    """Parse JSON payload into checkpoint row."""
    state = RuntimeState.model_validate_json(payload)
    return checkpoint_record_from_state(state)


def checkpoint_record_from_payload(payload: object) -> CheckpointRecord | None:
    """Parse mapping payload (dict-like JSON object) into checkpoint row."""
    state = RuntimeState.model_validate(payload)
    return checkpoint_record_from_state(state)


def runtime_event_from_json(payload: str) -> RuntimeEvent:
    """Parse JSON payload into runtime event."""
    return RuntimeEvent.model_validate_json(payload)


def runtime_event_from_payload(payload: object) -> RuntimeEvent:
    """Parse mapping payload into runtime event."""
    return RuntimeEvent.model_validate(payload)


__all__ = [
    "checkpoint_record_from_json",
    "checkpoint_record_from_payload",
    "checkpoint_record_from_state",
    "runtime_event_from_json",
    "runtime_event_from_payload",
]
