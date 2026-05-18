"""Runtime event log abstraction for durable runner skeleton."""

from __future__ import annotations

from agent_driver.contracts.events import RuntimeEvent


class InMemoryEventLog:
    """Append-only in-memory event log per run."""

    def __init__(self) -> None:
        self._events_by_run: dict[str, list[RuntimeEvent]] = {}

    def append(self, event: RuntimeEvent) -> None:
        """Append one runtime event."""
        self._events_by_run.setdefault(event.run_id, []).append(event)

    def list_for_run(self, run_id: str) -> list[RuntimeEvent]:
        """Return all events currently stored for run."""
        return list(self._events_by_run.get(run_id, []))
