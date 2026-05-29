"""Shared assertions for runtime store conformance tests."""

from __future__ import annotations

from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage import CheckpointStore


def assert_checkpoint_save_load_round_trip(
    *,
    store: CheckpointStore,
    graph_id: str,
    node_id: str,
    state: RuntimeState,
) -> None:
    """Assert checkpoint save/load round-trip for any backend."""
    ref = store.save(graph_id=graph_id, node_id=node_id, state=state)
    loaded = store.load(ref.checkpoint_id)
    assert loaded is not None
    assert loaded.ref.checkpoint_id == ref.checkpoint_id
