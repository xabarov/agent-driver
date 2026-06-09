"""In-memory and SQLite backends for the memory store protocol."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock

from agent_driver.memory.provider import MemoryKind, MemoryRecord


class InMemoryMemoryStore:
    """Process-local memory store; records survive only for the process."""

    def __init__(self) -> None:
        self._by_session: dict[str, list[MemoryRecord]] = {}
        self._seq = 0
        self._lock = RLock()

    def append(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a record with a fresh monotonic ``seq``."""
        with self._lock:
            self._seq += 1
            stored = record.model_copy(update={"seq": self._seq})
            self._by_session.setdefault(stored.session_id, []).append(stored)
            return stored

    def list_for_session(
        self, session_id: str, *, limit: int | None = None
    ) -> list[MemoryRecord]:
        """Return records for a session newest-first, optionally capped."""
        with self._lock:
            records = list(reversed(self._by_session.get(session_id, [])))
        if limit is not None:
            return records[:limit]
        return records

    def clear(self, session_id: str) -> None:
        """Drop all records for a session."""
        with self._lock:
            self._by_session.pop(session_id, None)


class SqliteMemoryStore:
    """Durable SQLite-backed memory store keyed by session."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = RLock()
        if str(self._path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
                """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_session "
                "ON memories (session_id, seq)"
            )
            self._conn.commit()

    def append(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a record; the DB assigns the autoincrement ``seq``."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO memories (session_id, kind, text, metadata) "
                "VALUES (?, ?, ?, ?)",
                (
                    record.session_id,
                    record.kind.value,
                    record.text,
                    json.dumps(record.metadata),
                ),
            )
            self._conn.commit()
            seq = int(cursor.lastrowid or 0)
        return record.model_copy(update={"seq": seq})

    def list_for_session(
        self, session_id: str, *, limit: int | None = None
    ) -> list[MemoryRecord]:
        """Return records for a session newest-first, optionally capped."""
        sql = (
            "SELECT seq, session_id, kind, text, metadata FROM memories "
            "WHERE session_id = ? ORDER BY seq DESC"
        )
        params: tuple[object, ...] = (session_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (session_id, limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def clear(self, session_id: str) -> None:
        """Drop all records for a session."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM memories WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row_to_record(row: tuple) -> MemoryRecord:
        seq, session_id, kind, text, metadata = row
        return MemoryRecord(
            session_id=session_id,
            text=text,
            kind=MemoryKind(kind),
            metadata=json.loads(metadata) if metadata else {},
            seq=int(seq),
        )


__all__ = ["InMemoryMemoryStore", "SqliteMemoryStore"]
