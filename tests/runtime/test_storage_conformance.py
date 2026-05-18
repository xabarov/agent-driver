"""Shared conformance checks for runtime checkpoint/event backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import new_runtime_event
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.sqlite_store import SqliteRuntimeStore
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog


@dataclass(frozen=True)
class _BackendPair:
    name: str
    checkpoint_store: CheckpointStore
    event_log: RuntimeEventLog


def _build_backend_pair(name: str, tmp_path: Path) -> _BackendPair:
    if name == "memory":
        return _BackendPair(
            name="memory",
            checkpoint_store=InMemoryCheckpointStore(),
            event_log=InMemoryEventLog(),
        )
    if name == "sqlite":
        store = SqliteRuntimeStore(path=str(tmp_path / "runtime_conformance.db"))
        return _BackendPair(name="sqlite", checkpoint_store=store, event_log=store)
    raise ValueError(f"Unsupported backend '{name}'")


def _state(run_id: str) -> RuntimeState:
    return RuntimeState(
        run_input=AgentRunInput(
            input="hello",
            run_id=run_id,
            agent_id="agent-test",
            graph_preset="single_react",
        ),
        metadata={"next_step": "llm_call"},
    )


@pytest.mark.parametrize("backend_name", ["memory", "sqlite"])
def test_checkpoint_store_conformance_save_load_latest(
    tmp_path: Path, backend_name: str
):
    """Backends should share save/load/latest and parent-chain semantics."""
    backend = _build_backend_pair(backend_name, tmp_path)
    first = backend.checkpoint_store.save(
        graph_id="graph",
        node_id="run_started",
        state=_state("run_conformance"),
    )
    second = backend.checkpoint_store.save(
        graph_id="graph",
        node_id="llm_call",
        state=_state("run_conformance"),
    )
    loaded = backend.checkpoint_store.load(first.checkpoint_id)
    assert loaded is not None
    latest = backend.checkpoint_store.latest("run_conformance")
    assert latest is not None
    assert latest.ref.checkpoint_id == second.checkpoint_id
    assert latest.ref.parent_checkpoint_id == first.checkpoint_id
    listed = backend.checkpoint_store.list_checkpoints("run_conformance")
    assert [row.ref.checkpoint_id for row in listed] == [
        second.checkpoint_id,
        first.checkpoint_id,
    ]


@pytest.mark.parametrize("backend_name", ["memory", "sqlite"])
def test_event_log_conformance_order_and_after_seq(tmp_path: Path, backend_name: str):
    """Event backends should preserve order and support after_seq filtering."""
    backend = _build_backend_pair(backend_name, tmp_path)
    for seq in (1, 2, 3):
        backend.event_log.append(
            new_runtime_event(
                event_type=RuntimeEventType.NODE_COMPLETED,
                context={
                    "run_id": "run_evt_conformance",
                    "attempt_id": "attempt_1",
                    "seq": seq,
                },
            )
        )
    all_events = backend.event_log.list_for_run("run_evt_conformance")
    assert [event.seq for event in all_events] == [1, 2, 3]
    filtered = backend.event_log.list_for_run("run_evt_conformance", after_seq=1)
    assert [event.seq for event in filtered] == [2, 3]
