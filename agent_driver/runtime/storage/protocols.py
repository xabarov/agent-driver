"""Storage protocols and records for runtime checkpoints/events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.state import RuntimeState


@dataclass(frozen=True)
class CheckpointRecord:
    """Checkpoint row pairing reference and serialized runtime state."""

    ref: CheckpointRef
    state: RuntimeState


@dataclass(frozen=True)
class StorageCapabilities:
    """Backend capability flags used by runtime/docs selection logic."""

    transactional_writes: bool
    supports_branching: bool
    supports_retention: bool
    supports_snapshot_debug: bool = False


class CheckpointStore(Protocol):
    """Protocol for persisting and loading runtime checkpoints."""

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        """Persist runtime state and return checkpoint reference."""
        raise NotImplementedError

    def latest(self, run_id: str) -> CheckpointRecord | None:
        """Return latest checkpoint row for run, if any."""
        raise NotImplementedError

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint row by checkpoint identifier, if any."""
        raise NotImplementedError

    def list_checkpoints(
        self, run_id: str, *, limit: int | None = None
    ) -> list[CheckpointRecord]:
        """Return checkpoints for one run ordered newest-first."""
        raise NotImplementedError

    def snapshot_debug(self) -> Mapping[str, list[CheckpointRecord]]:
        """Return debug-only snapshot of all checkpoint rows."""
        raise NotImplementedError

    def capabilities(self) -> StorageCapabilities:
        """Return backend capability flags for operators/tests."""
        raise NotImplementedError


class RuntimeEventLog(Protocol):
    """Protocol for append-only runtime event stores."""

    def append(self, event: RuntimeEvent) -> None:
        """Persist one runtime event."""
        raise NotImplementedError

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        """Return run events, optionally filtering by sequence number."""
        raise NotImplementedError

    def next_seq(self, run_id: str) -> int:
        """Return the sequence number the next appended event should carry.

        This is a *peek*: repeated calls return the same value until an event is
        actually appended, so a caller may read it once for an event's payload and
        again when stamping the event without producing a gap. The store is the
        single serialization point for a run's log, so this is collision-safe
        across every appender (runner emit, finalization, SDK control injection).

        The default is a full-log scan; in-tree backends override it with an O(1)
        counter (in-memory) or an indexed ``MAX(seq)`` (SQL) so a long run never
        pays the O(n)-per-emit cost that materialized the whole log each time.
        """
        events = self.list_for_run(run_id)
        return (max(event.seq for event in events) + 1) if events else 1

    def capabilities(self) -> StorageCapabilities:
        """Return backend capability flags for operators/tests."""
        raise NotImplementedError


def next_event_seq(event_log: "RuntimeEventLog", run_id: str) -> int:
    """Peek the next event sequence, tolerating event logs without ``next_seq``.

    In-tree backends implement an O(1)/indexed ``next_seq``; external or test
    doubles that predate it (bare ``append``/``list_for_run`` duck types) fall back
    to the scan. Every seq consumer routes through here so the fast path is used
    whenever the backend offers it, with no hard dependency on the method existing.
    """
    peek = getattr(event_log, "next_seq", None)
    if callable(peek):
        return cast(int, peek(run_id))
    events = event_log.list_for_run(run_id)
    return (max(event.seq for event in events) + 1) if events else 1
