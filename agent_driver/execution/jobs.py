"""Duplicate-tolerant, fencing observation of execution jobs (EPIC-04).

Pure helpers over the job contracts: a :class:`JobObserver` accumulates ordered
events across reconnects while dropping duplicates and fencing stale
generations, flags a compaction gap so the caller falls back to the terminal
snapshot, and never lets a late/stale delivery become a normal observation.
"""

from __future__ import annotations

from agent_driver.contracts.execution_job import (
    ExecutionEvent,
    ExecutionEventCursor,
    ExecutionEventPage,
    ExecutionHandle,
    ExecutionTerminalSnapshot,
)


def fence_stale(handle: ExecutionHandle, execution_generation: str) -> bool:
    """True when data tagged ``execution_generation`` is from a stale generation
    for ``handle`` and must be fenced (recorded diagnostically at most)."""
    return execution_generation != handle.execution_generation


def initial_cursor(handle: ExecutionHandle) -> ExecutionEventCursor:
    """The cursor for a fresh observation (nothing committed yet)."""
    return ExecutionEventCursor(
        job_id=handle.job_id, execution_generation=handle.execution_generation
    )


class TerminalConflictError(Exception):
    """A second terminal snapshot for the same execution generation carried
    conflicting content — a backend protocol violation."""


class JobObserver:
    """Accumulates a job's events across reconnects, duplicate-tolerant and
    generation-fenced. Feed it each :class:`ExecutionEventPage`; it returns only
    the fresh, in-generation events and advances the durable cursor. On a gap it
    sets :attr:`needs_snapshot` so the caller uses the snapshot path instead of
    assuming contiguity."""

    def __init__(self, handle: ExecutionHandle) -> None:
        self._handle = handle
        self._seen: set[tuple[str, int]] = set()
        self._cursor = initial_cursor(handle)
        self._needs_snapshot = False
        self._complete = False
        self._terminal: ExecutionTerminalSnapshot | None = None

    @property
    def cursor(self) -> ExecutionEventCursor:
        return self._cursor

    @property
    def needs_snapshot(self) -> bool:
        return self._needs_snapshot

    @property
    def complete(self) -> bool:
        return self._complete

    def ingest(self, page: ExecutionEventPage) -> list[ExecutionEvent]:
        """Return the fresh, non-duplicate, in-generation events from ``page``.

        Stale-generation events are fenced (dropped); already-seen identities are
        dropped (duplicate-tolerant replay); a ``gap_detected`` page flags a
        snapshot fallback. The cursor advances to ``page.next_cursor``.
        """
        if page.gap_detected:
            self._needs_snapshot = True
        fresh: list[ExecutionEvent] = []
        for event in page.events:
            if fence_stale(self._handle, event.execution_generation):
                continue  # fenced: late data from a superseded generation
            key = event.identity_key()
            if key in self._seen:
                continue  # duplicate-tolerant
            self._seen.add(key)
            if event.terminal:
                self._complete = True
            fresh.append(event)
        self._cursor = page.next_cursor
        if page.complete:
            self._complete = True
        return fresh

    def resolve_terminal(
        self, snapshot: ExecutionTerminalSnapshot
    ) -> ExecutionTerminalSnapshot:
        """Record the terminal snapshot, rejecting a conflicting re-delivery.

        A duplicate terminal (same generation + state + exit code) is idempotent;
        conflicting content for the same execution generation raises
        :class:`TerminalConflictError` (a backend protocol violation). A snapshot
        from a stale generation is fenced (ignored, prior terminal kept).
        """
        if fence_stale(self._handle, snapshot.handle.execution_generation):
            return self._terminal or snapshot
        prev = self._terminal
        if prev is not None and (
            prev.state is not snapshot.state or prev.exit_code != snapshot.exit_code
        ):
            raise TerminalConflictError(
                f"conflicting terminal for {snapshot.handle.job_id}"
            )
        self._terminal = snapshot
        self._complete = True
        return snapshot


__all__ = [
    "JobObserver",
    "TerminalConflictError",
    "fence_stale",
    "initial_cursor",
]
