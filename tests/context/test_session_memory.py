"""Session memory contract and store integration tests."""

from __future__ import annotations

from agent_driver.context import (
    InMemoryArtifactStore,
    load_session_memory,
    save_session_memory,
)
from agent_driver.contracts import SessionMemory


def _memory(last_turn: int = 3) -> SessionMemory:
    return SessionMemory(
        memory_id="mem_a",
        session_id="sess_a",
        summary="Session summary",
        key_facts=["f1", "f2"],
        pending_tasks=["t1"],
        open_questions=["q1"],
        last_summarized_turn_index=last_turn,
    )


def test_session_memory_round_trip_via_artifact_store() -> None:
    """Session memory should persist and load through artifact store."""
    store = InMemoryArtifactStore()
    memory = _memory()
    save_session_memory(artifact_store=store, memory=memory)
    loaded = load_session_memory(artifact_store=store, session_id=memory.session_id)
    assert loaded is not None
    assert loaded.summary == memory.summary
    assert loaded.key_facts == memory.key_facts
