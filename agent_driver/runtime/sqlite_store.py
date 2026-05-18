"""SQLite-backed runtime checkpoint and event storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.checkpoint_factory import (
    CheckpointChain,
    build_checkpoint_ref,
)
from agent_driver.runtime.checkpoints import _prepare_seed_and_previous
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage import CheckpointRecord


class SqliteRuntimeStore:
    """SQLite store implementing checkpoint and event storage protocols."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
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
        self._conn.commit()

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
        row = CheckpointRecord(ref=checkpoint, state=state)
        payload = row.state.model_dump_json()
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
        state = RuntimeState.model_validate_json(row[0])
        if state.checkpoint is None:
            return None
        return CheckpointRecord(ref=state.checkpoint, state=state)

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint row by checkpoint identifier."""
        row = self._conn.execute(
            "SELECT payload FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return None
        state = RuntimeState.model_validate_json(row[0])
        if state.checkpoint is None:
            return None
        return CheckpointRecord(ref=state.checkpoint, state=state)

    def snapshot(self) -> dict[str, list[CheckpointRecord]]:
        """Return grouped checkpoint snapshot by run id."""
        grouped: dict[str, list[CheckpointRecord]] = {}
        rows = self._conn.execute("SELECT payload FROM checkpoints").fetchall()
        for (payload,) in rows:
            state = RuntimeState.model_validate_json(payload)
            if state.checkpoint is None:
                continue
            grouped.setdefault(state.checkpoint.run_id, []).append(
                CheckpointRecord(ref=state.checkpoint, state=state)
            )
        return grouped

    def append(self, event: RuntimeEvent) -> None:
        """Persist one runtime event row."""
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
            rows = self._conn.execute(
                """
                SELECT payload FROM run_events
                WHERE run_id = ?
                ORDER BY seq ASC
                """,
                (run_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload FROM run_events
                WHERE run_id = ? AND seq > ?
                ORDER BY seq ASC
                """,
                (run_id, after_seq),
            ).fetchall()
        return [RuntimeEvent.model_validate_json(payload) for (payload,) in rows]
