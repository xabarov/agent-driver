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

import sqlite3
from pathlib import Path
from threading import RLock


class SqliteStoreBase:
    """Connection + lock lifecycle shared by SQLite-backed stores."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = RLock()
        if str(self._path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
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
