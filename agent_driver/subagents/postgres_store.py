"""Postgres-backed :class:`SubagentStore` (opencode-adoption EPIC-11, Stage 2).

Puts durable subagent run/group state on the same PostgreSQL control plane as the
approval / abort / plan-artifact stores (``runtime/control/postgres.py``), instead of the
per-process SQLite backend — the substrate the epic targets for unifying the fragmented
subagent stacks. Implements the same :class:`~agent_driver.subagents.store.SubagentStore`
protocol (including Stage-1's ``find_run_by_child_run_id``), so it is a drop-in for
``RunnerConfig.subagent_store``.

``psycopg`` (v3) is imported lazily via the shared base, so importing this module is free
without the optional ``agent-driver[postgres]`` extra installed.
"""

from __future__ import annotations

import json
from typing import Any

from agent_driver.contracts.subagents import SubagentGroup, SubagentRun
from agent_driver.runtime.control.postgres import (
    PostgresControlStoreConfig,
    _PostgresControlStoreBase,
)


class PostgresSubagentStore(_PostgresControlStoreBase):
    """Durable subagent run/group store on the shared Postgres control plane."""

    _table_name = "subagent_runs"

    def __init__(self, *, config: PostgresControlStoreConfig) -> None:
        self._groups_table = f"{config.schema}.subagent_groups"
        super().__init__(config=config)

    def _init_schema(self, cur: Any) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                subagent_run_id TEXT PRIMARY KEY,
                parent_run_id TEXT NOT NULL,
                idempotency_key TEXT,
                child_run_id TEXT,
                payload JSONB NOT NULL
            )
            """
        )
        cur.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS subagent_runs_idem_idx
            ON {self._table} (parent_run_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS subagent_runs_child_idx
            ON {self._table} (child_run_id)
            WHERE child_run_id IS NOT NULL
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._groups_table} (
                group_id TEXT PRIMARY KEY,
                parent_run_id TEXT NOT NULL,
                payload JSONB NOT NULL
            )
            """
        )

    # -- groups -----------------------------------------------------------------

    def upsert_group(self, group: SubagentGroup) -> SubagentGroup:
        payload = json.dumps(group.model_dump(mode="json"), ensure_ascii=False)
        with self._connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self._groups_table} (group_id, parent_run_id, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (group_id) DO UPDATE SET
                    parent_run_id = EXCLUDED.parent_run_id,
                    payload = EXCLUDED.payload
                """,
                (group.group_id, group.parent_run_id, payload),
            )
        return group

    def list_groups(self, parent_run_id: str) -> list[SubagentGroup]:
        with self._connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT payload FROM {self._groups_table} "
                f"WHERE parent_run_id = %s ORDER BY group_id ASC",
                (parent_run_id,),
            )
            rows = cur.fetchall()
        return [SubagentGroup.model_validate(_payload(row)) for row in rows]

    # -- runs -------------------------------------------------------------------

    def upsert_run(
        self, run: SubagentRun, *, idempotency_key: str | None = None
    ) -> SubagentRun:
        with self._connect(autocommit=True) as conn, conn.cursor() as cur:
            if idempotency_key:
                # Reuse the existing subagent_run_id for this (parent, idempotency_key)
                # so a retried spawn updates the same row (mirrors SQLite/in-memory).
                cur.execute(
                    f"SELECT subagent_run_id FROM {self._table} "
                    f"WHERE parent_run_id = %s AND idempotency_key = %s LIMIT 1",
                    (run.parent_run_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    run = run.model_copy(
                        update={"subagent_run_id": existing["subagent_run_id"]}
                    )
            payload = json.dumps(run.model_dump(mode="json"), ensure_ascii=False)
            cur.execute(
                f"""
                INSERT INTO {self._table}
                    (subagent_run_id, parent_run_id, idempotency_key, child_run_id, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (subagent_run_id) DO UPDATE SET
                    parent_run_id = EXCLUDED.parent_run_id,
                    idempotency_key = EXCLUDED.idempotency_key,
                    child_run_id = EXCLUDED.child_run_id,
                    payload = EXCLUDED.payload
                """,
                (
                    run.subagent_run_id,
                    run.parent_run_id,
                    idempotency_key,
                    run.child_run_id,
                    payload,
                ),
            )
        return run

    def list_runs(self, parent_run_id: str) -> list[SubagentRun]:
        with self._connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT payload FROM {self._table} "
                f"WHERE parent_run_id = %s ORDER BY subagent_run_id ASC",
                (parent_run_id,),
            )
            rows = cur.fetchall()
        return [SubagentRun.model_validate(_payload(row)) for row in rows]

    def find_run_by_child_run_id(self, child_run_id: str) -> SubagentRun | None:
        if not child_run_id:
            return None
        with self._connect(autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT payload FROM {self._table} WHERE child_run_id = %s LIMIT 1",
                (child_run_id,),
            )
            row = cur.fetchone()
        return SubagentRun.model_validate(_payload(row)) if row is not None else None


def _payload(row: Any) -> Any:
    """Return the JSONB ``payload`` column as a dict (psycopg may hand back str or dict)."""
    value = row["payload"]
    return json.loads(value) if isinstance(value, str) else value


__all__ = ["PostgresSubagentStore"]
