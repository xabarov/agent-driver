"""Runtime event log abstraction for durable runner skeleton."""

from __future__ import annotations

from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.storage import RuntimeEventLog


class InMemoryEventLog(RuntimeEventLog):
    """Append-only in-memory event log per run."""

    def __init__(self) -> None:
        self._events_by_run: dict[str, list[RuntimeEvent]] = {}

    def append(self, event: RuntimeEvent) -> None:
        """Append one runtime event."""
        self._events_by_run.setdefault(event.run_id, []).append(event)

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        """Return run events, optionally filtering by sequence number."""
        events = list(self._events_by_run.get(run_id, []))
        if after_seq is None:
            return events
        return [event for event in events if event.seq > after_seq]
