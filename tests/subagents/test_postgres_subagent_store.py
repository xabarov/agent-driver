"""opencode-adoption EPIC-11 (Stage 2) — Postgres-backed SubagentStore.

The conformance round-trip is opt-in (needs a real Postgres via
``AGENT_DRIVER_RUN_POSTGRES_TESTS=1`` + ``AGENT_DRIVER_POSTGRES_DSN``); it was
live-verified against ``postgres:16`` during development. An offline test pins that the
store satisfies the ``SubagentStore`` protocol surface (including Stage-1's
``find_run_by_child_run_id``) without a database.
"""

from __future__ import annotations

import os

import pytest

from agent_driver.contracts.enums.subagents import (
    SubagentJoinPolicy,
    SubagentStatus,
)
from agent_driver.contracts.subagents import SubagentGroup, SubagentRun
from agent_driver.runtime.control.postgres import PostgresControlStoreConfig
from agent_driver.subagents import PostgresSubagentStore, SubagentStore


def _run(sub: str, parent: str, child: str) -> SubagentRun:
    return SubagentRun(
        subagent_run_id=sub,
        parent_run_id=parent,
        parent_attempt_id="a1",
        child_run_id=child,
        task_id="t",
        task_type="g",
        description="d",
        status=SubagentStatus.RUNNING,
    )


def test_satisfies_subagent_store_protocol() -> None:
    # Structural (offline): the class provides the full protocol surface.
    for method in (
        "upsert_group",
        "list_groups",
        "upsert_run",
        "list_runs",
        "find_run_by_child_run_id",
    ):
        assert callable(getattr(PostgresSubagentStore, method, None)), method
    assert isinstance(PostgresSubagentStore, type)
    # a SubagentStore-typed slot accepts it (Protocol is structural)
    _: type[SubagentStore] = PostgresSubagentStore  # noqa: F841


def _pg_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_POSTGRES_TESTS", "").strip() == "1"


@pytest.mark.live
@pytest.mark.skipif(
    not _pg_enabled(), reason="requires AGENT_DRIVER_RUN_POSTGRES_TESTS=1"
)
def test_postgres_subagent_store_round_trip() -> None:
    dsn = os.getenv("AGENT_DRIVER_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AGENT_DRIVER_POSTGRES_DSN is required")
    store = PostgresSubagentStore(
        config=PostgresControlStoreConfig(dsn=dsn, auto_create_schema=True)
    )
    store.upsert_run(_run("sub_1", "par_1", "child_aaa"))
    store.upsert_run(_run("sub_2", "par_1", "child_bbb"))
    store.upsert_run(_run("sub_3", "par_2", "child_ccc"))

    assert {r.subagent_run_id for r in store.list_runs("par_1")} == {"sub_1", "sub_2"}
    # Stage-1 lookup across parents, durable across store instances
    assert store.find_run_by_child_run_id("child_bbb").subagent_run_id == "sub_2"
    assert store.find_run_by_child_run_id("child_ccc").parent_run_id == "par_2"
    assert store.find_run_by_child_run_id("missing") is None

    # idempotency: same key reuses the row id
    store.upsert_run(_run("sub_X", "par_9", "child_x"), idempotency_key="k1")
    reused = store.upsert_run(_run("sub_Y", "par_9", "child_y"), idempotency_key="k1")
    assert reused.subagent_run_id == "sub_X"
    assert len(store.list_runs("par_9")) == 1

    store.upsert_group(
        SubagentGroup(
            group_id="g1",
            parent_run_id="par_1",
            parent_attempt_id="a1",
            join_policy=SubagentJoinPolicy.WAIT_ALL,
        )
    )
    assert [g.group_id for g in store.list_groups("par_1")] == ["g1"]

    # a fresh instance (process restart) still resolves the durable child id
    reopened = PostgresSubagentStore(
        config=PostgresControlStoreConfig(dsn=dsn, auto_create_schema=True)
    )
    assert reopened.find_run_by_child_run_id("child_aaa").subagent_run_id == "sub_1"
