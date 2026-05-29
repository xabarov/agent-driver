"""Conformance tests for session store backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_driver.context.sessions import InMemorySessionStore, SqliteSessionStore
from agent_driver.context.sessions.protocols import SessionStore
from agent_driver.contracts import ChatMessage, SessionRef, SessionTurn, TurnDigest


@dataclass(frozen=True)
class _Backend:
    name: str
    store: SessionStore


def _backend(name: str, tmp_path: Path) -> _Backend:
    if name == "memory":
        return _Backend(name="memory", store=InMemorySessionStore())
    if name == "sqlite":
        return _Backend(
            name="sqlite", store=SqliteSessionStore(path=str(tmp_path / "sessions.db"))
        )
    raise ValueError(f"Unsupported backend '{name}'")


@pytest.mark.parametrize("backend_name", ["memory", "sqlite"])
def test_session_store_round_trip(tmp_path: Path, backend_name: str) -> None:
    """Backends should persist session ref, ordered turns and digests."""
    backend = _backend(backend_name, tmp_path)
    session = SessionRef(
        session_id="sess_1",
        run_id="run_1",
        attempt_id="attempt_1",
        metadata={"workspace": "ws"},
    )
    backend.store.upsert_session(session)
    backend.store.append_turn(
        SessionTurn(
            session_id="sess_1",
            turn_index=1,
            message=ChatMessage(role="user", content="world"),
        )
    )
    backend.store.append_turn(
        SessionTurn(
            session_id="sess_1",
            turn_index=0,
            message=ChatMessage(role="user", content="hello"),
        )
    )
    backend.store.save_digest(
        "sess_1", TurnDigest(digest_id="dig_1", turn_index=0, summary="sum")
    )

    loaded = backend.store.get_session("sess_1")
    turns = backend.store.list_turns("sess_1")
    latest = backend.store.latest_turn("sess_1")
    digests = backend.store.list_digests("sess_1")
    assert loaded is not None
    assert loaded.metadata["workspace"] == "ws"
    assert [turn.turn_index for turn in turns] == [0, 1]
    assert latest is not None
    assert latest.turn_index == 1
    assert digests[0].digest_id == "dig_1"
