"""SQLite-backed runtime checkpoint and event storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.checkpoint_factory import (
    CheckpointChain,
    build_checkpoint_ref,
)
from agent_driver.runtime.checkpoints import _prepare_seed_and_previous
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage import CheckpointRecord, StorageCapabilities
from agent_driver.runtime.storage.payloads import (
    checkpoint_record_from_json,
    checkpoint_record_from_state,
    runtime_event_from_json,
)

SQLITE_CAPABILITIES = StorageCapabilities(
    transactional_writes=True,
    supports_branching=False,
    supports_retention=True,
    supports_snapshot_debug=True,
)
SQLITE_SCHEMA_VERSION = 1


class SqliteRuntimeStore:
    """SQLite store implementing checkpoint and event storage protocols."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = RLock()
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS runtime_schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """)
            self._conn.execute(
                """
                INSERT OR REPLACE INTO runtime_schema_meta (key, value)
                VALUES (?, ?)
                """,
                ("runtime_schema_version", str(SQLITE_SCHEMA_VERSION)),
            )
            self._conn.commit()

    def schema_version(self) -> int:
        """Return current sqlite runtime schema version."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM runtime_schema_meta WHERE key = ?",
                ("runtime_schema_version",),
            ).fetchone()
        if row is None:
            return 0
        return int(row[0])

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        """Persist runtime state and return checkpoint reference."""
        seed, previous = _prepare_seed_and_previous(
            latest_loader=self.latest,
            graph_id=graph_id,
            node_id=node_id,
            storage_backend="sqlite",
            state=state,
        )
        checkpoint = build_checkpoint_ref(
            seed=seed,
            chain=CheckpointChain(previous_row=previous),
        )
        state = state.model_copy(update={"checkpoint": checkpoint})
        record = checkpoint_record_from_state(state)
        if record is None:
            raise RuntimeError("Checkpoint payload missing checkpoint reference")
        payload = record.state.model_dump_json()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (checkpoint_id, run_id, payload)
                VALUES (?, ?, ?)
                """,
                (checkpoint.checkpoint_id, checkpoint.run_id, payload),
            )
            self._conn.commit()
        return checkpoint

    def latest(self, run_id: str) -> CheckpointRecord | None:
        """Return latest checkpoint for run based on created_at ordering."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT payload
                FROM checkpoints
                WHERE run_id = ?
                ORDER BY json_extract(payload, '$.checkpoint.created_at') DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return checkpoint_record_from_json(row[0])

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint row by checkpoint identifier."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return checkpoint_record_from_json(row[0])

    def list_checkpoints(
        self, run_id: str, *, limit: int | None = None
    ) -> list[CheckpointRecord]:
        """Return checkpoints for run in newest-first order."""
        if limit is None:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT payload
                    FROM checkpoints
                    WHERE run_id = ?
                    ORDER BY json_extract(payload, '$.checkpoint.created_at') DESC
                    """,
                    (run_id,),
                ).fetchall()
        else:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT payload
                    FROM checkpoints
                    WHERE run_id = ?
                    ORDER BY json_extract(payload, '$.checkpoint.created_at') DESC
                    LIMIT ?
                    """,
                    (run_id, limit),
                ).fetchall()
        result: list[CheckpointRecord] = []
        for (payload,) in rows:
            record = checkpoint_record_from_json(payload)
            if record is not None:
                result.append(record)
        return result

    def snapshot_debug(self) -> dict[str, list[CheckpointRecord]]:
        """Return grouped checkpoint snapshot by run id (debug helper)."""
        grouped: dict[str, list[CheckpointRecord]] = {}
        with self._lock:
            rows = self._conn.execute("SELECT payload FROM checkpoints").fetchall()
        for (payload,) in rows:
            record = checkpoint_record_from_json(payload)
            if record is None:
                continue
            grouped.setdefault(record.ref.run_id, []).append(record)
        return grouped

    def capabilities(self) -> StorageCapabilities:
        """Return capabilities for SQLite backend."""
        return SQLITE_CAPABILITIES

    def append(self, event: RuntimeEvent) -> None:
        """Persist one runtime event row."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO run_events (event_id, run_id, seq, payload)
                VALUES (?, ?, ?, ?)
                """,
                (event.event_id, event.run_id, event.seq, event.model_dump_json()),
            )
            self._conn.commit()

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        """Return run events ordered by seq, optionally after given sequence."""
        if after_seq is None:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT payload FROM run_events
                    WHERE run_id = ?
                    ORDER BY seq ASC
                    """,
                    (run_id,),
                ).fetchall()
        else:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT payload FROM run_events
                    WHERE run_id = ? AND seq > ?
                    ORDER BY seq ASC
                    """,
                    (run_id, after_seq),
                ).fetchall()
        return [runtime_event_from_json(payload) for (payload,) in rows]
