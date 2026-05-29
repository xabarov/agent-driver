"""Unit tests for postgres runtime SQL builders."""

from __future__ import annotations

from agent_driver.runtime.storage.postgres_sql import (
    prune_checkpoints_before_sql,
    prune_events_before_sql,
    schema_migrations,
)


def test_schema_migrations_are_versioned_and_ordered() -> None:
    """Migrations list should be deterministic and monotonic."""
    migrations = schema_migrations(
        schema="public",
        checkpoints_table="public.runtime_checkpoints",
        events_table="public.runtime_events",
    )
    assert migrations
    versions = [version for version, _sql in migrations]
    assert versions == sorted(versions)
    assert versions[0] == 1


def test_prune_sql_targets_expected_tables() -> None:
    """Retention SQL should delete by created_at on target tables."""
    events_sql = prune_events_before_sql(events_table="public.runtime_events")
    checkpoints_sql = prune_checkpoints_before_sql(
        checkpoints_table="public.runtime_checkpoints"
    )
    assert "DELETE FROM public.runtime_events" in events_sql
    assert "created_at < %s" in events_sql
    assert "DELETE FROM public.runtime_checkpoints" in checkpoints_sql
    assert "created_at < %s" in checkpoints_sql
