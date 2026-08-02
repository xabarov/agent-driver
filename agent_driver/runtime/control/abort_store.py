"""Durable abort lifecycle ledger (U4 A/D — epic 052).

``RunAbortHandle`` is process-local: its flag vanishes on restart, and the
durable ``DurableAbortRequestRecord.observed`` field was never actually set, so
a host could not tell — after a crash — whether a stop request was *observed and
cancelled the run*, or whether the *run had already completed before the stop
landed*. This ledger makes that lifecycle real and queryable after a restart:

    requested → observed → cancelled | completed_before_cancel

* ``request_abort`` records a durable, actor/reason-correlated stop request
  (issuable from another process before the runner has even observed it).
* ``mark_observed`` records that the runner observed the abort (sets
  ``observed=True`` — the transition the old record never made).
* ``resolve`` records the truthful terminal outcome: ``cancelled`` when the abort
  stopped the run, ``completed_before_cancel`` when the run finished first.

Two implementations share one contract (mirrors ``approval_store``): a
lock-guarded dict and a SQLite table keyed by ``run_id`` (durable + cross-process).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_driver.persistence.sqlite import open_sqlite_connection


class AbortLifecycleState(str, Enum):
    """Durable abort lifecycle states."""

    REQUESTED = "requested"
    OBSERVED = "observed"
    CANCELLED = "cancelled"
    COMPLETED_BEFORE_CANCEL = "completed_before_cancel"


_TERMINAL_STATES = frozenset(
    {AbortLifecycleState.CANCELLED, AbortLifecycleState.COMPLETED_BEFORE_CANCEL}
)


@dataclass(frozen=True, slots=True)
class AbortRecord:
    """One run's durable abort lifecycle record."""

    run_id: str
    state: AbortLifecycleState
    observed: bool
    reason: str | None = None
    actor: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


@runtime_checkable
class AbortLifecycleStore(Protocol):
    """Durable, restart-queryable abort lifecycle ledger."""

    def request_abort(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        """Record a durable stop request (idempotent per run)."""
        ...

    def mark_observed(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        """Record that the runner observed the abort (sets observed=True)."""
        ...

    def resolve(self, run_id: str, *, cancelled: bool) -> AbortRecord | None:
        """Record the terminal outcome; None when no abort was ever in play."""
        ...

    def get(self, run_id: str) -> AbortRecord | None:
        """Return the run's abort record, or None."""
        ...


class InMemoryAbortLifecycleStore:
    """Lock-guarded dict implementation (single process / unit tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, AbortRecord] = {}

    def request_abort(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        with self._lock:
            existing = self._rows.get(run_id)
            if existing is not None:
                return existing
            record = AbortRecord(
                run_id=run_id,
                state=AbortLifecycleState.REQUESTED,
                observed=False,
                reason=reason,
                actor=actor,
            )
            self._rows[run_id] = record
            return record

    def mark_observed(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        with self._lock:
            existing = self._rows.get(run_id)
            if existing is not None and existing.is_terminal:
                return existing
            record = AbortRecord(
                run_id=run_id,
                state=AbortLifecycleState.OBSERVED,
                observed=True,
                reason=reason or (existing.reason if existing else None),
                actor=actor or (existing.actor if existing else None),
            )
            self._rows[run_id] = record
            return record

    def resolve(self, run_id: str, *, cancelled: bool) -> AbortRecord | None:
        with self._lock:
            existing = self._rows.get(run_id)
            if existing is None:
                return None
            if existing.is_terminal:
                return existing
            state = (
                AbortLifecycleState.CANCELLED
                if cancelled
                else AbortLifecycleState.COMPLETED_BEFORE_CANCEL
            )
            record = AbortRecord(
                run_id=run_id,
                state=state,
                observed=existing.observed or cancelled,
                reason=existing.reason,
                actor=existing.actor,
            )
            self._rows[run_id] = record
            return record

    def get(self, run_id: str) -> AbortRecord | None:
        with self._lock:
            return self._rows.get(run_id)


class SqliteAbortLifecycleStore:
    """SQLite abort lifecycle ledger — durable + cross-process."""

    def __init__(self, *, path: str) -> None:
        self._path = str(Path(path))
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self._path, row_factory=sqlite3.Row)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS abort_lifecycle (
                    run_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    observed INTEGER NOT NULL DEFAULT 0,
                    reason TEXT,
                    actor TEXT
                )
                """
            )

    def _row_to_record(self, row: sqlite3.Row) -> AbortRecord:
        return AbortRecord(
            run_id=row["run_id"],
            state=AbortLifecycleState(row["state"]),
            observed=bool(row["observed"]),
            reason=row["reason"],
            actor=row["actor"],
        )

    def get(self, run_id: str) -> AbortRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM abort_lifecycle WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def request_abort(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO abort_lifecycle (run_id, state, observed, reason, actor)
                VALUES (?, ?, 0, ?, ?)
                """,
                (run_id, AbortLifecycleState.REQUESTED.value, reason, actor),
            )
        got = self.get(run_id)
        assert got is not None  # just inserted or already present
        return got

    def mark_observed(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        with self._connect() as conn:
            # Create-or-advance, but never move a terminal row backwards.
            conn.execute(
                """
                INSERT INTO abort_lifecycle (run_id, state, observed, reason, actor)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state = CASE
                        WHEN abort_lifecycle.state IN (?, ?) THEN abort_lifecycle.state
                        ELSE ?
                    END,
                    observed = 1,
                    reason = COALESCE(abort_lifecycle.reason, excluded.reason),
                    actor = COALESCE(abort_lifecycle.actor, excluded.actor)
                """,
                (
                    run_id,
                    AbortLifecycleState.OBSERVED.value,
                    reason,
                    actor,
                    AbortLifecycleState.CANCELLED.value,
                    AbortLifecycleState.COMPLETED_BEFORE_CANCEL.value,
                    AbortLifecycleState.OBSERVED.value,
                ),
            )
        got = self.get(run_id)
        assert got is not None
        return got

    def resolve(self, run_id: str, *, cancelled: bool) -> AbortRecord | None:
        terminal = (
            AbortLifecycleState.CANCELLED
            if cancelled
            else AbortLifecycleState.COMPLETED_BEFORE_CANCEL
        )
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE abort_lifecycle
                SET state = ?, observed = observed OR ?
                WHERE run_id = ? AND state NOT IN (?, ?)
                """,
                (
                    terminal.value,
                    1 if cancelled else 0,
                    run_id,
                    AbortLifecycleState.CANCELLED.value,
                    AbortLifecycleState.COMPLETED_BEFORE_CANCEL.value,
                ),
            )
            _ = cursor
        return self.get(run_id)


__all__ = [
    "AbortLifecycleState",
    "AbortLifecycleStore",
    "AbortRecord",
    "InMemoryAbortLifecycleStore",
    "SqliteAbortLifecycleStore",
]
