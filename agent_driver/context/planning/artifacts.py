"""Plan artifact helpers and in-memory persistence."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import sqlite3
from pathlib import Path
from typing import Protocol

from agent_driver.contracts.context import PlanArtifact
from agent_driver.contracts.enums import PlanningModeState


def utc_now_iso() -> str:
    """Return a stable UTC timestamp string for contracts."""
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def plan_content_hash(content: str) -> str:
    """Hash plan content for approval/replay integrity."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PlanArtifactStore(Protocol):
    """Persistence contract for force-planning artifacts."""

    def put(self, artifact: PlanArtifact) -> PlanArtifact:
        """Create or replace a plan artifact."""

    def get(self, plan_id: str) -> PlanArtifact | None:
        """Load one plan artifact by id."""

    def list_for_run(self, run_id: str) -> list[PlanArtifact]:
        """List artifacts for a run in insertion order."""


class InMemoryPlanArtifactStore(PlanArtifactStore):
    """Process-local plan artifact store for tests and simple hosts."""

    def __init__(self) -> None:
        self._by_id: dict[str, PlanArtifact] = {}
        self._ids_by_run: dict[str, list[str]] = {}

    def put(self, artifact: PlanArtifact) -> PlanArtifact:
        exists = artifact.plan_id in self._by_id
        self._by_id[artifact.plan_id] = artifact
        if not exists:
            self._ids_by_run.setdefault(artifact.run_id, []).append(artifact.plan_id)
        return artifact

    def get(self, plan_id: str) -> PlanArtifact | None:
        return self._by_id.get(plan_id)

    def list_for_run(self, run_id: str) -> list[PlanArtifact]:
        return [
            self._by_id[plan_id]
            for plan_id in self._ids_by_run.get(run_id, [])
            if plan_id in self._by_id
        ]


class SqlitePlanArtifactStore(PlanArtifactStore):
    """SQLite plan artifact store for local durable approval workflows."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS plan_artifacts (
                plan_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """)
        self._conn.commit()

    def put(self, artifact: PlanArtifact) -> PlanArtifact:
        """Create or replace a plan artifact."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO plan_artifacts (
                plan_id, run_id, created_at, payload
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                artifact.plan_id,
                artifact.run_id,
                artifact.created_at,
                artifact.model_dump_json(),
            ),
        )
        self._conn.commit()
        return artifact

    def get(self, plan_id: str) -> PlanArtifact | None:
        """Load one plan artifact by id."""
        row = self._conn.execute(
            "SELECT payload FROM plan_artifacts WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return PlanArtifact.model_validate_json(row[0])

    def list_for_run(self, run_id: str) -> list[PlanArtifact]:
        """List artifacts for a run in creation order."""
        rows = self._conn.execute(
            """
            SELECT payload FROM plan_artifacts
            WHERE run_id = ?
            ORDER BY created_at ASC, plan_id ASC
            """,
            (run_id,),
        ).fetchall()
        return [PlanArtifact.model_validate_json(payload) for (payload,) in rows]


def create_plan_artifact(
    *,
    plan_id: str,
    run_id: str,
    agent_id: str,
    content: str = "",
    thread_id: str | None = None,
    path: str | None = None,
    metadata: dict[str, object] | None = None,
) -> PlanArtifact:
    """Create a collecting plan artifact with deterministic content hash."""
    now = utc_now_iso()
    return PlanArtifact(
        plan_id=plan_id,
        run_id=run_id,
        thread_id=thread_id,
        agent_id=agent_id,
        path=path,
        content=content,
        content_hash=plan_content_hash(content),
        status=PlanningModeState.COLLECTING,
        created_at=now,
        updated_at=now,
        metadata=dict(metadata or {}),
    )


def update_plan_artifact_content(
    artifact: PlanArtifact, *, content: str, path: str | None = None
) -> PlanArtifact:
    """Return artifact with updated content and reset collecting status."""
    return artifact.model_copy(
        update={
            "content": content,
            "content_hash": plan_content_hash(content),
            "path": path if path is not None else artifact.path,
            "status": PlanningModeState.COLLECTING,
            "updated_at": utc_now_iso(),
            "approved_at": None,
            "approved_by": None,
            "rejected_at": None,
            "rejected_by": None,
            "rejection_reason": None,
        }
    )


def mark_plan_awaiting_approval(artifact: PlanArtifact) -> PlanArtifact:
    """Return artifact marked as ready for human approval."""
    return artifact.model_copy(
        update={
            "status": PlanningModeState.AWAITING_APPROVAL,
            "updated_at": utc_now_iso(),
        }
    )


def approve_plan_artifact(
    artifact: PlanArtifact, *, approved_by: str | None = None
) -> PlanArtifact:
    """Return artifact marked approved."""
    now = utc_now_iso()
    return artifact.model_copy(
        update={
            "status": PlanningModeState.APPROVED,
            "updated_at": now,
            "approved_at": now,
            "approved_by": approved_by,
            "rejected_at": None,
            "rejected_by": None,
            "rejection_reason": None,
        }
    )


def reject_plan_artifact(
    artifact: PlanArtifact,
    *,
    rejected_by: str | None = None,
    reason: str | None = None,
) -> PlanArtifact:
    """Return artifact marked rejected."""
    now = utc_now_iso()
    return artifact.model_copy(
        update={
            "status": PlanningModeState.REJECTED,
            "updated_at": now,
            "rejected_at": now,
            "rejected_by": rejected_by,
            "rejection_reason": reason,
            "approved_at": None,
            "approved_by": None,
        }
    )


__all__ = [
    "InMemoryPlanArtifactStore",
    "PlanArtifactStore",
    "SqlitePlanArtifactStore",
    "approve_plan_artifact",
    "create_plan_artifact",
    "mark_plan_awaiting_approval",
    "plan_content_hash",
    "reject_plan_artifact",
    "update_plan_artifact_content",
    "utc_now_iso",
]
