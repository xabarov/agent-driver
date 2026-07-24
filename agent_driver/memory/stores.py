"""In-memory and SQLite backends for the memory store protocol."""

from __future__ import annotations

import json
from threading import RLock

from agent_driver.memory.provider import MemoryKind, MemoryRecord
from agent_driver.persistence import SqliteStoreBase


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

    def replace_session(
        self, session_id: str, records: list[MemoryRecord]
    ) -> list[MemoryRecord]:
        """Atomically swap a session's records (epic 031 consolidation seam).

        ``records`` are given newest-first; they are stored oldest-first with
        fresh monotonic seqs so ``list_for_session`` keeps returning newest-first.
        """
        with self._lock:
            stored: list[MemoryRecord] = []
            new_list: list[MemoryRecord] = []
            for record in reversed(records):
                self._seq += 1
                copy = record.model_copy(update={"seq": self._seq})
                new_list.append(copy)
                stored.append(copy)
            self._by_session[session_id] = new_list
            return list(reversed(stored))


class SqliteMemoryStore(SqliteStoreBase):
    """Durable SQLite-backed memory store keyed by session."""

    def _init_schema(self) -> None:
        self._execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
            "kind TEXT NOT NULL, text TEXT NOT NULL, metadata TEXT NOT NULL)"
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_session "
            "ON memories (session_id, seq)"
        )

    def append(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a record; the DB assigns the autoincrement ``seq``."""
        cursor = self._execute(
            "INSERT INTO memories (session_id, kind, text, metadata) "
            "VALUES (?, ?, ?, ?)",
            (
                record.session_id,
                record.kind.value,
                record.text,
                json.dumps(record.metadata),
            ),
        )
        return record.model_copy(update={"seq": int(cursor.lastrowid or 0)})

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
        return [self._row_to_record(row) for row in self._query(sql, params)]

    def clear(self, session_id: str) -> None:
        """Drop all records for a session."""
        self._execute("DELETE FROM memories WHERE session_id = ?", (session_id,))

    def replace_session(
        self, session_id: str, records: list[MemoryRecord]
    ) -> list[MemoryRecord]:
        """Atomically swap a session's records (epic 031 consolidation seam).

        Deletes the session's rows and re-inserts ``records`` oldest-first so the
        DB assigns fresh ascending seqs; returns them newest-first.
        """
        self._execute("DELETE FROM memories WHERE session_id = ?", (session_id,))
        stored: list[MemoryRecord] = []
        for record in reversed(records):
            cursor = self._execute(
                "INSERT INTO memories (session_id, kind, text, metadata) "
                "VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    record.kind.value,
                    record.text,
                    json.dumps(record.metadata),
                ),
            )
            stored.append(record.model_copy(update={"seq": int(cursor.lastrowid or 0)}))
        return list(reversed(stored))

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
