"""Shared SQLite connection plumbing for keyed/append stores.

Several stores (runtime checkpoints, sessions, artifacts, memory, scheduler
jobs, …) had independently re-implemented the same boilerplate: open a
connection with ``check_same_thread=False``, enable WAL (except for an
in-memory DB), guard access with a re-entrant lock, and commit on write.

:class:`SqliteStoreBase` extracts exactly that connection/locking layer — not
a schema. Subclasses declare their own tables in :meth:`_init_schema` and run
their own SQL through the locked :meth:`_execute` / :meth:`_query` helpers, so
each store keeps the schema it needs while sharing the plumbing.

Intentionally stdlib-only (no ``agent_driver`` imports) so any package can
build on it without creating an import cycle.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)

# Write-lock patience: how long a writer waits for a sibling holding the DB before
# giving up. A storage-contention timeout must NOT abort an otherwise-healthy turn —
# a sibling can legitimately hold the DB for multi-second stretches (VACUUM after an
# auto-prune, a WAL truncate-checkpoint on close, a mixed-version process during a
# rolling deploy). SQLite's native busy_timeout is time-based (better than counting
# attempts). Reference: hermes 8da8a7887 (production: 10.8GB db, 9 concurrent procs).
DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS = 30.0


class WalUnsupportedError(RuntimeError):
    """Raised when WAL was required but the filesystem silently refused it."""


def open_sqlite_connection(
    path: str | Path,
    *,
    check_same_thread: bool = True,
    busy_timeout_seconds: float = DEFAULT_SQLITE_BUSY_TIMEOUT_SECONDS,
    journal_mode: str = "WAL",
    require_wal: bool = False,
    row_factory: object | None = None,
) -> sqlite3.Connection:
    """Open a SQLite connection with durability defaults (single canonical opener).

    Every store opens here so the same hardening applies everywhere:
    - ``busy_timeout`` gives writers time-based patience under contention instead of
      failing fast and aborting a healthy turn;
    - ``journal_mode=WAL`` is verified against the value SQLite actually returns — on
      NFS/SMB/overlay filesystems ``PRAGMA journal_mode=WAL`` silently returns ``delete``
      (reader-blocks-writer), and degraded concurrency must never be silent. ``require_wal``
      turns that into a typed :class:`WalUnsupportedError` instead of a warning.

    Stdlib-only (no ``agent_driver`` imports) so any package can build on it.
    """
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    if row_factory is not None:
        conn.row_factory = row_factory  # type: ignore[assignment]
    if busy_timeout_seconds and busy_timeout_seconds > 0:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_seconds * 1000)};")
    if journal_mode and str(path) != ":memory:":
        row = conn.execute(f"PRAGMA journal_mode={journal_mode};").fetchone()
        effective = str(row[0]).lower() if row else ""
        if journal_mode.lower() == "wal" and effective != "wal":
            message = (
                f"SQLite journal_mode=WAL requested for {path} but the filesystem "
                f"returned {effective!r}; running with degraded concurrency "
                "(reader blocks writer)."
            )
            if require_wal:
                conn.close()
                raise WalUnsupportedError(message)
            logger.warning(message)
    return conn


class SqliteStoreBase:
    """Connection + lock lifecycle shared by SQLite-backed stores."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = open_sqlite_connection(self._path, check_same_thread=False)
        self._lock = RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables/indexes. Override in subclasses."""

    def _execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        """Run a write statement under the lock and commit."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def _query(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
        """Run a read statement under the lock and return all rows."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()


__all__ = ["SqliteStoreBase"]
