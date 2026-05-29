"""Tests for runtime stream projection/backfill helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_driver.contracts import RuntimeEventType, new_runtime_event
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.sqlite_store import SqliteRuntimeStore
from agent_driver.runtime.stream import backfill_stream_events, project_runtime_events


def test_project_runtime_events_maps_type_and_seq() -> None:
    """Projection should preserve ordering and map event type."""
    events = [
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_a", "attempt_id": "att_1", "seq": 1},
        ),
        new_runtime_event(
            event_type=RuntimeEventType.LLM_CALL_COMPLETED,
            context={"run_id": "run_a", "attempt_id": "att_1", "seq": 2},
            options={"payload": {"model": "fake"}},
        ),
    ]
    projected = project_runtime_events(events)
    assert [item.seq for item in projected] == [1, 2]
    assert projected[1].event == "llm_call_completed"
    assert projected[1].data["model"] == "fake"


@pytest.mark.parametrize(
    "event_type",
    [
        RuntimeEventType.RUN_STARTED,
        RuntimeEventType.RUN_RESUMED,
        RuntimeEventType.RUN_PAUSED,
        RuntimeEventType.RUN_COMPLETED,
        RuntimeEventType.LLM_CALL_STARTED,
        RuntimeEventType.TOKEN_DELTA,
        RuntimeEventType.LLM_CALL_COMPLETED,
        RuntimeEventType.TOOL_CALL_STARTED,
        RuntimeEventType.TOOL_CALL_COMPLETED,
        RuntimeEventType.INTERRUPT_REQUESTED,
        RuntimeEventType.CONTROL_REQUESTED,
        RuntimeEventType.COMMAND_QUEUED,
        RuntimeEventType.COMMAND_DEQUEUED,
        RuntimeEventType.COMMAND_CANCELLED,
        RuntimeEventType.CONTROL_APPLIED,
        RuntimeEventType.WARNING,
        RuntimeEventType.RUN_FAILED,
    ],
)
def test_project_runtime_events_covers_lifecycle_categories(
    event_type: RuntimeEventType,
) -> None:
    """Projection should preserve lifecycle event type names across categories."""
    projected = project_runtime_events(
        [
            new_runtime_event(
                event_type=event_type,
                context={"run_id": "run_lifecycle", "attempt_id": "att_1", "seq": 1},
            )
        ]
    )
    assert projected[0].event == event_type.value


def test_backfill_stream_events_respects_after_seq() -> None:
    """Backfill helper should honor after_seq filter."""
    log = InMemoryEventLog()
    log.append(
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_b", "attempt_id": "att_1", "seq": 1},
        )
    )
    log.append(
        new_runtime_event(
            event_type=RuntimeEventType.LLM_CALL_STARTED,
            context={"run_id": "run_b", "attempt_id": "att_1", "seq": 2},
        )
    )
    rows = backfill_stream_events(log, run_id="run_b", after_seq=1)
    assert len(rows) == 1
    assert rows[0].seq == 2


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_backfill_stream_events_supported_for_memory_and_sqlite(
    tmp_path: Path, backend: str
) -> None:
    """Backfill should work with both in-memory and sqlite runtime logs."""
    if backend == "sqlite":
        event_log = SqliteRuntimeStore(path=str(tmp_path / "stream_backfill.sqlite3"))
    else:
        event_log = InMemoryEventLog()
    event_log.append(
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_store", "attempt_id": "att_1", "seq": 1},
        )
    )
    event_log.append(
        new_runtime_event(
            event_type=RuntimeEventType.TOKEN_DELTA,
            context={"run_id": "run_store", "attempt_id": "att_1", "seq": 2},
            options={"payload": {"delta_text": "hello"}},
        )
    )
    rows = backfill_stream_events(event_log, run_id="run_store", after_seq=1)
    assert len(rows) == 1
    assert rows[0].event == RuntimeEventType.TOKEN_DELTA.value
    assert rows[0].stream_id == "run_store:2"
