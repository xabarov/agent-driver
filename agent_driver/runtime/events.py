"""Runtime event log abstraction for durable runner skeleton."""

from __future__ import annotations

from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.storage import RuntimeEventLog, StorageCapabilities


class InMemoryEventLog(RuntimeEventLog):
    """Append-only in-memory event log per run."""

    def __init__(self) -> None:
        self._events_by_run: dict[str, list[RuntimeEvent]] = {}
        # Per-run high-water seq, advanced on append, so ``next_seq`` is O(1)
        # instead of re-scanning the (unboundedly growing) event list each emit.
        self._max_seq_by_run: dict[str, int] = {}

    def append(self, event: RuntimeEvent) -> None:
        """Append one runtime event."""
        self._events_by_run.setdefault(event.run_id, []).append(event)
        prior = self._max_seq_by_run.get(event.run_id, 0)
        if event.seq > prior:
            self._max_seq_by_run[event.run_id] = event.seq

    def next_seq(self, run_id: str) -> int:
        """Peek the next seq in O(1) from the maintained high-water mark."""
        return self._max_seq_by_run.get(run_id, 0) + 1

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        """Return run events, optionally filtering by sequence number."""
        events = list(self._events_by_run.get(run_id, []))
        if after_seq is None:
            return events
        return [event for event in events if event.seq > after_seq]

    def capabilities(self) -> StorageCapabilities:
        """Return capabilities for in-memory event log backend."""
        return StorageCapabilities(
            transactional_writes=False,
            supports_branching=False,
            supports_retention=False,
            supports_snapshot_debug=False,
        )
