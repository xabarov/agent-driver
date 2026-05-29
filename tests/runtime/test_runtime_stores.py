"""Runtime store-level tests for in-memory/sqlite components."""

from __future__ import annotations

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import new_runtime_event
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.runtime import (
    InMemoryCheckpointStore,
    InMemoryEventLog,
    SqliteRuntimeStore,
)
from agent_driver.runtime.state import RuntimeState
from tests.runtime.store_assertions import assert_checkpoint_save_load_round_trip


def test_inmemory_checkpoint_store_save_and_latest() -> None:
    """Checkpoint store should persist and return latest row per run."""
    store = InMemoryCheckpointStore()
    input_payload = AgentRunInput(
        input="hello",
        agent_id="agent",
        graph_preset="single_react",
        run_id="run_1",
    )
    state = RuntimeState(run_input=input_payload)
    first_ref = store.save(graph_id="g1", node_id="n1", state=state)
    second_ref = store.save(graph_id="g1", node_id="n2", state=state)
    latest = store.latest("run_1")
    assert latest is not None
    assert latest.ref.checkpoint_id == second_ref.checkpoint_id
    assert latest.ref.parent_checkpoint_id == first_ref.checkpoint_id
    loaded = store.load(first_ref.checkpoint_id)
    assert loaded is not None
    assert loaded.ref.checkpoint_id == first_ref.checkpoint_id
    listed = store.list_checkpoints("run_1")
    assert [row.ref.checkpoint_id for row in listed] == [
        second_ref.checkpoint_id,
        first_ref.checkpoint_id,
    ]
    debug_snapshot = store.snapshot_debug()
    assert "run_1" in debug_snapshot
    caps = store.capabilities()
    assert caps.supports_snapshot_debug


def test_inmemory_event_log_after_seq_filter() -> None:
    """Event log should support filtering by sequence number."""
    events = InMemoryEventLog()
    run_id = "run_evt_1"
    for seq in (1, 2, 3):
        events.append(
            new_runtime_event(
                event_type=RuntimeEventType.NODE_COMPLETED,
                context={"run_id": run_id, "attempt_id": "att_1", "seq": seq},
            )
        )
    assert len(events.list_for_run(run_id)) == 3
    filtered = events.list_for_run(run_id, after_seq=1)
    assert [event.seq for event in filtered] == [2, 3]
    caps = events.capabilities()
    assert not caps.transactional_writes


def test_sqlite_runtime_store_round_trip(tmp_path) -> None:
    """SQLite runtime store should persist checkpoints and events."""
    store = SqliteRuntimeStore(path=str(tmp_path / "runtime.db"))
    run_input = AgentRunInput(
        input="hello",
        run_id="run_sqlite_1",
        agent_id="agent-test",
        graph_preset="single_react",
    )
    state = RuntimeState(run_input=run_input, metadata={"next_step": "llm_call"})
    assert_checkpoint_save_load_round_trip(
        store=store,
        graph_id="single_agent_runtime",
        node_id="run_started",
        state=state,
    )
    listed = store.list_checkpoints("run_sqlite_1", limit=1)
    assert len(listed) == 1
    assert listed[0].ref.run_id == "run_sqlite_1"
    debug_snapshot = store.snapshot_debug()
    assert "run_sqlite_1" in debug_snapshot
    caps = store.capabilities()
    assert caps.transactional_writes

    event = new_runtime_event(
        event_type=RuntimeEventType.RUN_STARTED,
        context={"run_id": "run_sqlite_1", "attempt_id": "attempt_1", "seq": 1},
    )
    store.append(event)
    events = store.list_for_run("run_sqlite_1")
    assert len(events) == 1
    assert events[0].event_id == event.event_id
