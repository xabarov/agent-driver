"""Atomic, durable approval-consumption ledger (U3 B/C — epic 051).

The resume path's default duplicate-approval guard is a TOCTOU read of the
latest checkpoint's ``consumed_approvals`` plus an optimistic
``expected_checkpoint_id``: it recognises a duplicate only AFTER the first
approval's post-consume checkpoint commits, so two clients approving the same
interrupt in the pre-commit window can both drive the run and execute the tool
twice. This module closes that window with a **compare-and-swap** ledger: the
first ``try_consume`` for an interrupt wins (an atomic INSERT), any concurrent or
later duplicate loses and is told the tool must NOT run again — the exactly-once
gate that also survives a crash between consume and result (the row is written
BEFORE the tool executes).

Two implementations share one contract:

* :class:`InMemoryApprovalConsumptionStore` — a lock-guarded dict, for a single
  process / unit tests.
* :class:`SqliteApprovalConsumptionStore` — a ``UNIQUE``-constrained table whose
  ``INSERT OR IGNORE`` is the cross-process CAS (mirrors the idempotency index on
  ``SqliteSubagentStore``); durable across restarts.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_driver.persistence.sqlite import open_sqlite_connection


class ConsumeStatus(str, Enum):
    """Outcome of an attempt to consume an approval."""

    CONSUMED = "consumed"  # this caller won the CAS; it may drive the tool
    DUPLICATE = "duplicate"  # already consumed with the same decision; do NOT re-run
    CONFLICT = "conflict"  # a different decision already consumed this interrupt


@dataclass(frozen=True, slots=True)
class ApprovalConsumeRequest:
    """One attempt to consume the approval of an interrupt."""

    run_id: str
    interrupt_id: str
    decision: str
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumeOutcome:
    """Result of :meth:`ApprovalConsumptionStore.try_consume`."""

    status: ConsumeStatus
    prior_decision: str | None = None
    prior_result_ref: str | None = None
    detail: str | None = None

    @property
    def is_first(self) -> bool:
        """True only for the single caller that won the consume."""
        return self.status is ConsumeStatus.CONSUMED


@runtime_checkable
class ApprovalConsumptionStore(Protocol):
    """Durable, atomic one-time approval-consumption ledger."""

    def try_consume(self, request: ApprovalConsumeRequest) -> ConsumeOutcome:
        """Atomically claim the approval; exactly one caller gets CONSUMED."""
        ...

    def record_result(
        self, *, run_id: str, interrupt_id: str, result_ref: str
    ) -> None:
        """Attach the terminal result identity to a consumed approval."""
        ...

    def get(
        self, *, run_id: str, interrupt_id: str
    ) -> ConsumeOutcome | None:
        """Return the recorded consumption for an interrupt, or None."""
        ...


class InMemoryApprovalConsumptionStore:
    """Lock-guarded dict implementation (single process / unit tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, str | None]] = {}
        self._by_key: dict[tuple[str, str], tuple[str, str]] = {}

    def _find_existing(
        self, request: ApprovalConsumeRequest
    ) -> dict[str, str | None] | None:
        pk = (request.run_id, request.interrupt_id)
        existing = self._rows.get(pk)
        if existing is None and request.idempotency_key is not None:
            mapped = self._by_key.get((request.run_id, request.idempotency_key))
            if mapped is not None:
                existing = self._rows.get(mapped)
        return existing

    def try_consume(self, request: ApprovalConsumeRequest) -> ConsumeOutcome:
        with self._lock:
            existing = self._find_existing(request)
            if existing is not None:
                if existing["decision"] != request.decision:
                    return ConsumeOutcome(
                        ConsumeStatus.CONFLICT,
                        prior_decision=existing["decision"],
                        detail="approval already consumed with a different decision",
                    )
                return ConsumeOutcome(
                    ConsumeStatus.DUPLICATE,
                    prior_decision=existing["decision"],
                    prior_result_ref=existing["result_ref"],
                )
            pk = (request.run_id, request.interrupt_id)
            self._rows[pk] = {
                "decision": request.decision,
                "result_ref": None,
            }
            if request.idempotency_key is not None:
                self._by_key[(request.run_id, request.idempotency_key)] = pk
            return ConsumeOutcome(ConsumeStatus.CONSUMED)

    def record_result(
        self, *, run_id: str, interrupt_id: str, result_ref: str
    ) -> None:
        with self._lock:
            row = self._rows.get((run_id, interrupt_id))
            if row is not None:
                row["result_ref"] = result_ref

    def get(
        self, *, run_id: str, interrupt_id: str
    ) -> ConsumeOutcome | None:
        with self._lock:
            row = self._rows.get((run_id, interrupt_id))
            if row is None:
                return None
            return ConsumeOutcome(
                ConsumeStatus.DUPLICATE,
                prior_decision=row["decision"],
                prior_result_ref=row["result_ref"],
            )


class SqliteApprovalConsumptionStore:
    """SQLite CAS ledger — durable + cross-process (INSERT OR IGNORE is the swap)."""

    def __init__(self, *, path: str) -> None:
        self._path = str(Path(path))
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self._path, row_factory=sqlite3.Row)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_consumptions (
                    run_id TEXT NOT NULL,
                    interrupt_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    decision TEXT NOT NULL,
                    result_ref TEXT,
                    PRIMARY KEY (run_id, interrupt_id)
                )
                """
            )
            # A duplicate carrying the same idempotency key (even a different
            # interrupt id) must also lose the CAS.
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS approval_consumptions_key_idx
                ON approval_consumptions(run_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )

    def try_consume(self, request: ApprovalConsumeRequest) -> ConsumeOutcome:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO approval_consumptions
                    (run_id, interrupt_id, idempotency_key, decision)
                VALUES (?, ?, ?, ?)
                """,
                (
                    request.run_id,
                    request.interrupt_id,
                    request.idempotency_key,
                    request.decision,
                ),
            )
            if cursor.rowcount == 1:
                return ConsumeOutcome(ConsumeStatus.CONSUMED)
            # The insert was ignored: a row already exists on the primary key or
            # the idempotency-key index. Read it back to classify.
            row = conn.execute(
                """
                SELECT decision, result_ref FROM approval_consumptions
                WHERE run_id = ? AND interrupt_id = ?
                """,
                (request.run_id, request.interrupt_id),
            ).fetchone()
            if row is None and request.idempotency_key is not None:
                row = conn.execute(
                    """
                    SELECT decision, result_ref FROM approval_consumptions
                    WHERE run_id = ? AND idempotency_key = ?
                    """,
                    (request.run_id, request.idempotency_key),
                ).fetchone()
            if row is None:  # pragma: no cover - lost-then-vanished race edge
                return ConsumeOutcome(
                    ConsumeStatus.CONFLICT, detail="consume ignored but no row found"
                )
            if row["decision"] != request.decision:
                return ConsumeOutcome(
                    ConsumeStatus.CONFLICT,
                    prior_decision=row["decision"],
                    detail="approval already consumed with a different decision",
                )
            return ConsumeOutcome(
                ConsumeStatus.DUPLICATE,
                prior_decision=row["decision"],
                prior_result_ref=row["result_ref"],
            )

    def record_result(
        self, *, run_id: str, interrupt_id: str, result_ref: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE approval_consumptions SET result_ref = ?
                WHERE run_id = ? AND interrupt_id = ?
                """,
                (result_ref, run_id, interrupt_id),
            )

    def get(
        self, *, run_id: str, interrupt_id: str
    ) -> ConsumeOutcome | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT decision, result_ref FROM approval_consumptions
                WHERE run_id = ? AND interrupt_id = ?
                """,
                (run_id, interrupt_id),
            ).fetchone()
        if row is None:
            return None
        return ConsumeOutcome(
            ConsumeStatus.DUPLICATE,
            prior_decision=row["decision"],
            prior_result_ref=row["result_ref"],
        )


__all__ = [
    "ApprovalConsumeRequest",
    "ApprovalConsumptionStore",
    "ConsumeOutcome",
    "ConsumeStatus",
    "InMemoryApprovalConsumptionStore",
    "SqliteApprovalConsumptionStore",
]
