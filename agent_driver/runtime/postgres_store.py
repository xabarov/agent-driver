"""PostgreSQL-backed runtime checkpoint and event storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module
from typing import Any

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
    checkpoint_record_from_payload,
    runtime_event_from_payload,
)
from agent_driver.runtime.storage.postgres_sql import (
    schema_ddl,
    select_checkpoint_by_id_sql,
    select_checkpoints_sql,
    select_distinct_runs_sql,
    select_events_sql,
    select_latest_checkpoint_sql,
    select_schema_version_sql,
    upsert_checkpoint_sql,
    upsert_event_sql,
    upsert_schema_version_sql,
)

SCHEMA_VERSION = 1
POSTGRES_CAPABILITIES = StorageCapabilities(
    transactional_writes=True,
    supports_branching=False,
    supports_retention=True,
    supports_snapshot_debug=True,
)


def _pg_dependencies() -> tuple[Any, Any]:
    """Import psycopg dependencies lazily for optional postgres extra."""
    try:
        psycopg_module = import_module("psycopg")
        rows_module = import_module("psycopg.rows")
        connect = psycopg_module.connect
        dict_row = rows_module.dict_row
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PostgreSQL support requires optional dependency: pip install 'agent-driver[postgres]'"
        ) from exc
    return connect, dict_row


@dataclass(frozen=True)
class PostgresRuntimeStoreConfig:
    """Configuration for PostgreSQL runtime storage backend."""

    dsn: str
    auto_create_schema: bool = True
    schema: str = "public"


class PostgresRuntimeStore:
    """PostgreSQL store implementing checkpoint and event storage protocols."""

    def __init__(self, *, config: PostgresRuntimeStoreConfig) -> None:
        self._config = config
        self._checkpoints_table = f"{self._config.schema}.runtime_checkpoints"
        self._events_table = f"{self._config.schema}.runtime_events"
        if self._config.auto_create_schema:
            self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create schema objects required for runtime store."""
        connect, _ = _pg_dependencies()
        with connect(self._config.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    schema_ddl(
                        schema=self._config.schema,
                        checkpoints_table=self._checkpoints_table,
                        events_table=self._events_table,
                    )
                )
                cur.execute(
                    upsert_schema_version_sql(schema=self._config.schema),
                    ("runtime_schema_version", str(SCHEMA_VERSION)),
                )

    def schema_version(self) -> int:
        """Return current postgres runtime schema version."""
        connect, _ = _pg_dependencies()
        with connect(self._config.dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    select_schema_version_sql(schema=self._config.schema),
                    ("runtime_schema_version",),
                )
                row = cur.fetchone()
        if row is None:
            return 0
        return int(row[0])

    def save(
        self, *, graph_id: str, node_id: str | None, state: RuntimeState
    ) -> CheckpointRef:
        """Persist runtime state and return checkpoint reference."""
        connect, _ = _pg_dependencies()
        seed, previous = _prepare_seed_and_previous(
            latest_loader=self.latest,
            graph_id=graph_id,
            node_id=node_id,
            storage_backend="postgres",
            state=state,
        )
        checkpoint = build_checkpoint_ref(
            seed=seed,
            chain=CheckpointChain(previous_row=previous),
        )
        state_with_checkpoint = state.model_copy(update={"checkpoint": checkpoint})
        payload = state_with_checkpoint.model_dump(mode="json")
        payload_json = json.dumps(payload, ensure_ascii=False)
        with connect(self._config.dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    upsert_checkpoint_sql(checkpoints_table=self._checkpoints_table),
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.run_id,
                        payload_json,
                    ),
                )
            conn.commit()
        return checkpoint

    def latest(self, run_id: str) -> CheckpointRecord | None:
        """Return latest checkpoint row for run."""
        connect, dict_row = _pg_dependencies()
        with connect(self._config.dsn, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    select_latest_checkpoint_sql(
                        checkpoints_table=self._checkpoints_table
                    ),
                    (run_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return checkpoint_record_from_payload(row["payload"])

    def load(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Return checkpoint row by checkpoint identifier."""
        connect, dict_row = _pg_dependencies()
        with connect(self._config.dsn, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    select_checkpoint_by_id_sql(
                        checkpoints_table=self._checkpoints_table
                    ),
                    (checkpoint_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return checkpoint_record_from_payload(row["payload"])

    def list_checkpoints(
        self, run_id: str, *, limit: int | None = None
    ) -> list[CheckpointRecord]:
        """Return checkpoints for run in newest-first order."""
        connect, dict_row = _pg_dependencies()
        sql = select_checkpoints_sql(
            checkpoints_table=self._checkpoints_table,
            with_limit=limit is not None,
        )
        params: tuple[object, ...]
        if limit is None:
            params = (run_id,)
        else:
            params = (run_id, limit)
        with connect(self._config.dsn, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        result: list[CheckpointRecord] = []
        for row in rows:
            record = checkpoint_record_from_payload(row["payload"])
            if record is not None:
                result.append(record)
        return result

    def snapshot_debug(self) -> dict[str, list[CheckpointRecord]]:
        """Return grouped debug snapshot of checkpoint rows by run id."""
        connect, dict_row = _pg_dependencies()
        grouped: dict[str, list[CheckpointRecord]] = {}
        with connect(self._config.dsn, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    select_distinct_runs_sql(checkpoints_table=self._checkpoints_table)
                )
                rows = cur.fetchall()
        for row in rows:
            grouped[row["run_id"]] = self.list_checkpoints(row["run_id"])
        return grouped

    def capabilities(self) -> StorageCapabilities:
        """Return capabilities for PostgreSQL backend."""
        return POSTGRES_CAPABILITIES

    def append(self, event: RuntimeEvent) -> None:
        """Persist one runtime event row."""
        connect, _ = _pg_dependencies()
        payload = event.model_dump(mode="json")
        payload_json = json.dumps(payload, ensure_ascii=False)
        with connect(self._config.dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    upsert_event_sql(events_table=self._events_table),
                    (event.event_id, event.run_id, event.seq, payload_json),
                )
            conn.commit()

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        """Return run events ordered by seq, optionally after given sequence."""
        connect, dict_row = _pg_dependencies()
        sql = select_events_sql(
            events_table=self._events_table, with_after_seq=after_seq is not None
        )
        params: tuple[object, ...]
        if after_seq is None:
            params = (run_id,)
        else:
            params = (run_id, after_seq)
        with connect(self._config.dsn, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [runtime_event_from_payload(row["payload"]) for row in rows]
