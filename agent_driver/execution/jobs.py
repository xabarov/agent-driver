"""Duplicate-tolerant, fencing observation of execution jobs (EPIC-04).

Pure helpers over the job contracts: a :class:`JobObserver` accumulates ordered
events across reconnects while dropping duplicates and fencing stale
generations, flags a compaction gap so the caller falls back to the terminal
snapshot, and never lets a late/stale delivery become a normal observation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from agent_driver.contracts.execution import ExecutionCommandRequest
from agent_driver.contracts.execution_job import (
    ExecutionEvent,
    ExecutionEventCursor,
    ExecutionEventPage,
    ExecutionHandle,
    ExecutionTerminalSnapshot,
)
from agent_driver.execution.errors import (
    ExecutionError,
    IndeterminateExecutionError,
)

if TYPE_CHECKING:
    from agent_driver.execution.protocol import JobCapableBackend


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


def persist_job_recovery(
    handle: ExecutionHandle, cursor: ExecutionEventCursor
) -> dict[str, Any]:
    """Serialize the SAFE, non-secret handle + cursor for durable checkpoint
    state, so a restarted run can re-attach and resume observation without
    re-dispatching. Mirrors how the lease ref is persisted in run metadata."""
    return {
        "handle": handle.model_dump(mode="json"),
        "cursor": cursor.model_dump(mode="json"),
    }


def restore_job_recovery(
    raw: dict[str, Any],
) -> tuple[ExecutionHandle, ExecutionEventCursor] | None:
    """Rebuild ``(handle, cursor)`` from persisted recovery state, or ``None``
    when the payload is missing/malformed (fail closed, never crash a resume)."""
    if not isinstance(raw, dict):
        return None
    try:
        handle = ExecutionHandle.model_validate(raw["handle"])
        cursor = ExecutionEventCursor.model_validate(raw["cursor"])
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    return handle, cursor


class JobSession:
    """Drive one execution job start→observe→terminal with reconnect + fencing.

    - ``start`` is idempotent and lost-start-safe: a transport fault on start is
      resolved by ``lookup_job(idempotency_key)``; if still unresolved the
      dispatch is INDETERMINATE (raised) and MUST NOT be blindly re-dispatched.
    - ``observe_to_terminal`` pages events through a :class:`JobObserver`
      (duplicate-tolerant, generation-fenced); a compaction gap falls back to the
      terminal snapshot; the terminal is resolved without re-dispatch.
    """

    def __init__(self, backend: "JobCapableBackend") -> None:
        self._backend = backend

    async def start(self, request: ExecutionCommandRequest) -> ExecutionHandle:
        key = request.identity.request_id
        try:
            return await self._backend.start_job(request)
        except ExecutionError:
            # The start reply may have been lost while the job actually started;
            # resolve by idempotency key rather than starting a second one.
            found = await self._backend.lookup_job(key)
            if found is not None:
                return found
            raise IndeterminateExecutionError(
                f"job start for {key} is indeterminate; not re-dispatched"
            ) from None

    async def observe_to_terminal(
        self,
        handle: ExecutionHandle,
        *,
        on_event: Callable[[ExecutionEvent], None] | None = None,
        start_cursor: ExecutionEventCursor | None = None,
        max_pages: int = 1000,
    ) -> ExecutionTerminalSnapshot:
        """Observe from ``start_cursor`` (or the initial cursor) to terminal.

        Fresh, in-generation, non-duplicate events are handed to ``on_event``.
        On a gap, or when the pages are exhausted, the terminal snapshot resolves
        the outcome (fencing a stale-generation or conflicting terminal)."""
        observer = JobObserver(handle)
        cursor = start_cursor or initial_cursor(handle)
        pages = 0
        while pages < max_pages:
            page = await self._backend.observe(handle, cursor)
            for event in observer.ingest(page):
                if on_event is not None:
                    on_event(event)
            cursor = observer.cursor
            if observer.needs_snapshot or page.complete or observer.complete:
                break
            if not page.events:
                break  # no more output available without a terminal
            pages += 1
        snapshot = await self._backend.snapshot(handle)
        return observer.resolve_terminal(snapshot)


__all__ = [
    "JobObserver",
    "JobSession",
    "TerminalConflictError",
    "fence_stale",
    "initial_cursor",
    "persist_job_recovery",
    "restore_job_recovery",
]
