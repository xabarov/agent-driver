"""SQLite-backed artifact/context stores."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_driver.context.artifacts.protocols import ArtifactStore, ContextStore
from agent_driver.contracts.context import ContextArtifactRef, StoredArtifact


class SqliteArtifactStore(ArtifactStore):
    """SQLite artifact persistence."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS context_artifacts (
                artifact_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """)
        self._conn.commit()

    def put(self, artifact: StoredArtifact) -> ContextArtifactRef:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO context_artifacts (artifact_id, kind, payload)
            VALUES (?, ?, ?)
            """,
            (
                artifact.ref.artifact_id,
                artifact.ref.kind.value,
                artifact.model_dump_json(),
            ),
        )
        self._conn.commit()
        return artifact.ref

    def get(self, artifact_id: str) -> StoredArtifact | None:
        row = self._conn.execute(
            "SELECT payload FROM context_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredArtifact.model_validate_json(row[0])

    def list_for_kind(self, kind: str) -> list[StoredArtifact]:
        rows = self._conn.execute(
            "SELECT payload FROM context_artifacts WHERE kind = ?", (kind,)
        ).fetchall()
        return [StoredArtifact.model_validate_json(payload) for (payload,) in rows]


class SqliteContextStore(ContextStore):
    """SQLite run->artifact reference mapping."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS run_artifact_refs (
                run_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, artifact_id)
            )
            """)
        self._conn.commit()

    def attach_artifact(self, run_id: str, artifact_ref: ContextArtifactRef) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO run_artifact_refs (run_id, artifact_id, payload)
            VALUES (?, ?, ?)
            """,
            (run_id, artifact_ref.artifact_id, artifact_ref.model_dump_json()),
        )
        self._conn.commit()

    def list_artifacts(self, run_id: str) -> list[ContextArtifactRef]:
        rows = self._conn.execute(
            "SELECT payload FROM run_artifact_refs WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [ContextArtifactRef.model_validate_json(payload) for (payload,) in rows]
