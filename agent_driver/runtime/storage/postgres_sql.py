"""SQL builders for PostgreSQL runtime store."""

from __future__ import annotations


def schema_ddl(*, schema: str, checkpoints_table: str, events_table: str) -> str:
    """Return DDL for runtime postgres schema bootstrap."""
    return f"""
    CREATE SCHEMA IF NOT EXISTS {schema};
    CREATE TABLE IF NOT EXISTS {checkpoints_table} (
        checkpoint_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS runtime_checkpoints_run_created_idx
        ON {checkpoints_table} (run_id, created_at DESC);

    CREATE TABLE IF NOT EXISTS {events_table} (
        event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        seq BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS runtime_events_run_seq_idx
        ON {events_table} (run_id, seq ASC);

    CREATE TABLE IF NOT EXISTS {schema}.runtime_schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """


def upsert_schema_version_sql(*, schema: str) -> str:
    """Upsert schema version row."""
    return f"""
    INSERT INTO {schema}.runtime_schema_meta (key, value)
    VALUES (%s, %s)
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """


def select_schema_version_sql(*, schema: str) -> str:
    """Select current schema version row."""
    return f"""
    SELECT value
    FROM {schema}.runtime_schema_meta
    WHERE key = %s
    """


def upsert_checkpoint_sql(*, checkpoints_table: str) -> str:
    """Upsert one checkpoint payload row."""
    return f"""
    INSERT INTO {checkpoints_table} (checkpoint_id, run_id, payload)
    VALUES (%s, %s, %s::jsonb)
    ON CONFLICT (checkpoint_id) DO UPDATE
        SET payload = EXCLUDED.payload
    """


def select_latest_checkpoint_sql(*, checkpoints_table: str) -> str:
    """Select latest checkpoint payload by run."""
    return f"""
    SELECT payload
    FROM {checkpoints_table}
    WHERE run_id = %s
    ORDER BY created_at DESC
    LIMIT 1
    """


def select_checkpoint_by_id_sql(*, checkpoints_table: str) -> str:
    """Select checkpoint payload by checkpoint id."""
    return f"""
    SELECT payload
    FROM {checkpoints_table}
    WHERE checkpoint_id = %s
    """


def select_checkpoints_sql(*, checkpoints_table: str, with_limit: bool) -> str:
    """Select checkpoints by run ordered newest-first."""
    if with_limit:
        return f"""
        SELECT payload
        FROM {checkpoints_table}
        WHERE run_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """
    return f"""
    SELECT payload
    FROM {checkpoints_table}
    WHERE run_id = %s
    ORDER BY created_at DESC
    """


def select_distinct_runs_sql(*, checkpoints_table: str) -> str:
    """Select distinct run ids from checkpoint table."""
    return f"SELECT DISTINCT run_id FROM {checkpoints_table}"


def upsert_event_sql(*, events_table: str) -> str:
    """Upsert one runtime event payload row."""
    return f"""
    INSERT INTO {events_table} (event_id, run_id, seq, payload)
    VALUES (%s, %s, %s, %s::jsonb)
    ON CONFLICT (event_id) DO UPDATE
        SET payload = EXCLUDED.payload
    """


def select_events_sql(*, events_table: str, with_after_seq: bool) -> str:
    """Select runtime events for run (optionally after seq)."""
    if with_after_seq:
        return f"""
        SELECT payload
        FROM {events_table}
        WHERE run_id = %s AND seq > %s
        ORDER BY seq ASC
        """
    return f"""
    SELECT payload
    FROM {events_table}
    WHERE run_id = %s
    ORDER BY seq ASC
    """


__all__ = [
    "schema_ddl",
    "select_checkpoint_by_id_sql",
    "select_checkpoints_sql",
    "select_distinct_runs_sql",
    "select_events_sql",
    "select_latest_checkpoint_sql",
    "select_schema_version_sql",
    "upsert_checkpoint_sql",
    "upsert_event_sql",
    "upsert_schema_version_sql",
]
