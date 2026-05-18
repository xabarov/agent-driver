"""Runtime event contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import RuntimeEvent, RuntimeEventType, new_runtime_event


def test_runtime_event_sequence_must_be_positive() -> None:
    """Reject events with non-positive sequence numbers."""
    with pytest.raises(ValidationError):
        RuntimeEvent(
            event_id="evt_1",
            type=RuntimeEventType.RUN_STARTED,
            run_id="run_1",
            attempt_id="att_1",
            seq=0,
            created_at="2026-05-18T10:00:00Z",
            payload={},
        )


def test_runtime_event_payload_must_be_json_serializable() -> None:
    """Reject event payload values that cannot be serialized to JSON."""
    with pytest.raises(ValidationError):
        RuntimeEvent(
            event_id="evt_1",
            type=RuntimeEventType.RUN_STARTED,
            run_id="run_1",
            attempt_id="att_1",
            seq=1,
            created_at="2026-05-18T10:00:00Z",
            payload={"bad": object()},
        )


def test_new_runtime_event_produces_monotonic_sequence() -> None:
    """Build events with provided monotonic sequence values."""
    first = new_runtime_event(
        event_type=RuntimeEventType.NODE_STARTED,
        context={"run_id": "run_1", "attempt_id": "att_1", "seq": 1},
    )
    second = new_runtime_event(
        event_type=RuntimeEventType.NODE_COMPLETED,
        context={"run_id": "run_1", "attempt_id": "att_1", "seq": 2},
    )
    assert first.seq == 1
    assert second.seq == 2
    assert first.type == RuntimeEventType.NODE_STARTED
    assert second.type == RuntimeEventType.NODE_COMPLETED
