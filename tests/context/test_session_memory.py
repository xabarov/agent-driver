"""Session memory contract and store integration tests."""

from __future__ import annotations

from agent_driver.context import (
    InMemoryArtifactStore,
    extract_session_memory,
    load_session_memory,
    save_session_memory,
)
from agent_driver.contracts import SessionMemory, TurnDigest


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


def test_extract_session_memory_updates_when_turn_gap_met() -> None:
    """Extractor should refresh memory when enough new digests exist."""
    previous = _memory(last_turn=2)
    digests = [
        TurnDigest(digest_id="d1", turn_index=1, summary="old", references=[]),
        TurnDigest(
            digest_id="d2",
            turn_index=3,
            summary="todo: verify output?",
            references=["artifact_a"],
        ),
        TurnDigest(
            digest_id="d3",
            turn_index=4,
            summary="next step pending validation",
            references=["artifact_b"],
        ),
    ]
    out = extract_session_memory(
        session_id="sess_a",
        digests=digests,
        previous=previous,
        min_turn_gap=2,
    )
    assert out.updated is True
    assert out.memory is not None
    assert out.memory.last_summarized_turn_index == 4
    assert out.memory.source_digest_ids[-2:] == ["d2", "d3"]
    assert "artifact_a" in out.memory.source_artifact_ids


def test_extract_session_memory_skips_when_turn_gap_small() -> None:
    """Extractor should skip update when latest turn gap is below threshold."""
    previous = _memory(last_turn=3)
    digests = [
        TurnDigest(digest_id="d1", turn_index=3, summary="same", references=[]),
        TurnDigest(digest_id="d2", turn_index=4, summary="new", references=[]),
    ]
    out = extract_session_memory(
        session_id="sess_a",
        digests=digests,
        previous=previous,
        min_turn_gap=2,
    )
    assert out.updated is False
    assert out.reason == "turn_gap_below_threshold"
