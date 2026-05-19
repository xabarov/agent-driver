"""Opt-in PostgreSQL runtime store conformance checks."""

from __future__ import annotations

import os

import pytest

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import new_runtime_event
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.runtime.postgres_store import (
    PostgresRuntimeStore,
    PostgresRuntimeStoreConfig,
)
from agent_driver.runtime.state import RuntimeState
from tests.live_env import load_local_dotenv_for_live_tests
from tests.runtime.store_assertions import assert_checkpoint_save_load_round_trip

pytestmark = pytest.mark.live


def _pg_live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_POSTGRES_TESTS", "").strip() == "1"


load_local_dotenv_for_live_tests()


def _pg_dsn() -> str | None:
    return os.getenv("AGENT_DRIVER_POSTGRES_DSN")


@pytest.mark.skipif(
    not _pg_live_enabled(), reason="requires AGENT_DRIVER_RUN_POSTGRES_TESTS=1"
)
def test_postgres_runtime_store_round_trip() -> None:
    """Run opt-in PostgreSQL checkpoint/event storage round-trip."""
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("AGENT_DRIVER_POSTGRES_DSN is required")
    store = PostgresRuntimeStore(
        config=PostgresRuntimeStoreConfig(dsn=dsn, auto_create_schema=True)
    )
    assert store.schema_version() > 0
    state = RuntimeState(
        run_input=AgentRunInput(
            input="hello from postgres live",
            run_id="run_pg_live_1",
            agent_id="agent-postgres-live",
            graph_preset="single_react",
        ),
        metadata={"next_step": "llm_call", "lane": "postgres_live"},
    )
    round_trip_kwargs = {
        "store": store,
        "graph_id": "single_agent_runtime",
        "node_id": "run_started",
        "state": state,
    }
    assert_checkpoint_save_load_round_trip(**round_trip_kwargs)
    latest = store.latest("run_pg_live_1")
    assert latest is not None
    event = new_runtime_event(
        event_type=RuntimeEventType.RUN_STARTED,
        context={"run_id": "run_pg_live_1", "attempt_id": "attempt_1", "seq": 1},
    )
    store.append(event)
    events = store.list_for_run("run_pg_live_1")
    assert len(events) >= 1
