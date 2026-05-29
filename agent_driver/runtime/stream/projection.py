"""Projection helpers from durable runtime events to stream envelopes."""

from __future__ import annotations

from collections.abc import Iterable

from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.storage import RuntimeEventLog


def project_runtime_events(events: Iterable[RuntimeEvent]) -> list[RunStreamEvent]:
    """Project runtime event iterable into normalized stream events."""
    return [RunStreamEvent.from_runtime_event(event) for event in events]


def backfill_stream_events(
    event_log: RuntimeEventLog,
    *,
    run_id: str,
    after_seq: int | None = None,
) -> list[RunStreamEvent]:
    """Load persisted runtime events and project to stream envelopes."""
    return project_runtime_events(event_log.list_for_run(run_id, after_seq=after_seq))


__all__ = ["backfill_stream_events", "project_runtime_events"]
