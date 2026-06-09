"""SQLite-backed artifact/context stores."""

from __future__ import annotations

from agent_driver.context.artifacts.protocols import ArtifactStore, ContextStore
from agent_driver.contracts.context import ContextArtifactRef, StoredArtifact
from agent_driver.persistence import SqliteStoreBase


class SqliteArtifactStore(SqliteStoreBase, ArtifactStore):
    """SQLite artifact persistence."""

    def _init_schema(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS context_artifacts (
                artifact_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """)

    def put(self, artifact: StoredArtifact) -> ContextArtifactRef:
        self._execute(
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
        return artifact.ref

    def get(self, artifact_id: str) -> StoredArtifact | None:
        rows = self._query(
            "SELECT payload FROM context_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        if not rows:
            return None
        return StoredArtifact.model_validate_json(rows[0][0])

    def list_for_kind(self, kind: str) -> list[StoredArtifact]:
        rows = self._query(
            "SELECT payload FROM context_artifacts WHERE kind = ?", (kind,)
        )
        return [StoredArtifact.model_validate_json(payload) for (payload,) in rows]


class SqliteContextStore(SqliteStoreBase, ContextStore):
    """SQLite run->artifact reference mapping."""

    def _init_schema(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS run_artifact_refs (
                run_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (run_id, artifact_id)
            )
            """)

    def attach_artifact(self, run_id: str, artifact_ref: ContextArtifactRef) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO run_artifact_refs (run_id, artifact_id, payload)
            VALUES (?, ?, ?)
            """,
            (run_id, artifact_ref.artifact_id, artifact_ref.model_dump_json()),
        )

    def list_artifacts(self, run_id: str) -> list[ContextArtifactRef]:
        rows = self._query(
            "SELECT payload FROM run_artifact_refs WHERE run_id = ?", (run_id,)
        )
        return [ContextArtifactRef.model_validate_json(payload) for (payload,) in rows]
