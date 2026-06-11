"""Subagent run/group stores (in-memory and sqlite durable backends)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_driver.contracts.subagents import SubagentGroup, SubagentRun


class SubagentStore(Protocol):  # pylint: disable=too-few-public-methods
    """Storage protocol for subagent run/group lifecycle rows."""

    def upsert_group(self, group: SubagentGroup) -> SubagentGroup: ...
    def list_groups(self, parent_run_id: str) -> list[SubagentGroup]: ...
    def upsert_run(
        self, run: SubagentRun, *, idempotency_key: str | None = None
    ) -> SubagentRun: ...
    def list_runs(self, parent_run_id: str) -> list[SubagentRun]: ...


@dataclass(slots=True)
class InMemorySubagentStore:
    """In-memory subagent state store with idempotent child spawn."""

    _runs_by_parent: dict[str, list[SubagentRun]] = field(default_factory=dict)
    _groups_by_parent: dict[str, list[SubagentGroup]] = field(default_factory=dict)
    _run_by_idempotency: dict[tuple[str, str], SubagentRun] = field(
        default_factory=dict
    )

    def upsert_group(self, group: SubagentGroup) -> SubagentGroup:
        """Insert or replace subagent group row."""
        rows = self._groups_by_parent.setdefault(group.parent_run_id, [])
        for idx, existing in enumerate(rows):
            if existing.group_id == group.group_id:
                rows[idx] = group
                return group
        rows.append(group)
        return group

    def list_groups(self, parent_run_id: str) -> list[SubagentGroup]:
        """List group rows by parent run."""
        return list(self._groups_by_parent.get(parent_run_id, []))

    def upsert_run(
        self, run: SubagentRun, *, idempotency_key: str | None = None
    ) -> SubagentRun:
        """Insert or replace subagent run row with optional idempotency key."""
        if idempotency_key:
            dedup_key = (run.parent_run_id, idempotency_key)
            existing = self._run_by_idempotency.get(dedup_key)
            if existing is not None:
                run = run.model_copy(
                    update={"subagent_run_id": existing.subagent_run_id}
                )
                self._run_by_idempotency[dedup_key] = run
            else:
                self._run_by_idempotency[dedup_key] = run
        rows = self._runs_by_parent.setdefault(run.parent_run_id, [])
        for idx, existing in enumerate(rows):
            if existing.subagent_run_id == run.subagent_run_id:
                rows[idx] = run
                return run
        rows.append(run)
        return run

    def list_runs(self, parent_run_id: str) -> list[SubagentRun]:
        """List child run rows by parent run."""
        return list(self._runs_by_parent.get(parent_run_id, []))


class SqliteSubagentStore:
    """SQLite-backed subagent store for durable parent-child replay."""

    def __init__(self, *, path: str) -> None:
        self._path = str(Path(path))
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subagent_groups (
                    group_id TEXT PRIMARY KEY,
                    parent_run_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subagent_runs (
                    subagent_run_id TEXT PRIMARY KEY,
                    parent_run_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS subagent_runs_parent_idempotency_idx
                ON subagent_runs(parent_run_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """)

    def upsert_group(self, group: SubagentGroup) -> SubagentGroup:
        payload = json.dumps(group.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_groups (group_id, parent_run_id, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    parent_run_id=excluded.parent_run_id,
                    payload=excluded.payload
                """,
                (group.group_id, group.parent_run_id, payload),
            )
        return group

    def list_groups(self, parent_run_id: str) -> list[SubagentGroup]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM subagent_groups
                WHERE parent_run_id = ?
                ORDER BY group_id ASC
                """,
                (parent_run_id,),
            ).fetchall()
        return [SubagentGroup.model_validate_json(row["payload"]) for row in rows]

    def upsert_run(
        self, run: SubagentRun, *, idempotency_key: str | None = None
    ) -> SubagentRun:
        payload = json.dumps(run.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            if idempotency_key:
                existing = conn.execute(
                    """
                    SELECT payload FROM subagent_runs
                    WHERE parent_run_id = ? AND idempotency_key = ?
                    LIMIT 1
                    """,
                    (run.parent_run_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    existing_run = SubagentRun.model_validate_json(existing["payload"])
                    run = run.model_copy(
                        update={"subagent_run_id": existing_run.subagent_run_id}
                    )
                    payload = json.dumps(
                        run.model_dump(mode="json"), ensure_ascii=False
                    )
            conn.execute(
                """
                INSERT INTO subagent_runs (subagent_run_id, parent_run_id, idempotency_key, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(subagent_run_id) DO UPDATE SET
                    parent_run_id=excluded.parent_run_id,
                    idempotency_key=excluded.idempotency_key,
                    payload=excluded.payload
                """,
                (run.subagent_run_id, run.parent_run_id, idempotency_key, payload),
            )
        return run

    def list_runs(self, parent_run_id: str) -> list[SubagentRun]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM subagent_runs
                WHERE parent_run_id = ?
                ORDER BY subagent_run_id ASC
                """,
                (parent_run_id,),
            ).fetchall()
        return [SubagentRun.model_validate_json(row["payload"]) for row in rows]


__all__ = ["InMemorySubagentStore", "SqliteSubagentStore", "SubagentStore"]
