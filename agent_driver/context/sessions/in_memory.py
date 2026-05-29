"""In-memory session store implementation."""

from __future__ import annotations

from agent_driver.context.sessions.protocols import SessionStore
from agent_driver.contracts.context import SessionRef, SessionTurn, TurnDigest


class InMemorySessionStore(SessionStore):
    """Simple in-memory session and turn storage for tests/local runs."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRef] = {}
        self._turns: dict[str, list[SessionTurn]] = {}
        self._digests: dict[str, list[TurnDigest]] = {}

    def upsert_session(self, session: SessionRef) -> SessionRef:
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionRef | None:
        return self._sessions.get(session_id)

    def append_turn(self, turn: SessionTurn) -> SessionTurn:
        turns = self._turns.setdefault(turn.session_id, [])
        turns.append(turn)
        turns.sort(key=lambda item: item.turn_index)
        return turn

    def list_turns(self, session_id: str) -> list[SessionTurn]:
        return list(self._turns.get(session_id, []))

    def latest_turn(self, session_id: str) -> SessionTurn | None:
        turns = self._turns.get(session_id, [])
        if not turns:
            return None
        return turns[-1]

    def save_digest(self, session_id: str, digest: TurnDigest) -> TurnDigest:
        digests = self._digests.setdefault(session_id, [])
        digests.append(digest)
        digests.sort(key=lambda item: item.turn_index)
        return digest

    def list_digests(self, session_id: str) -> list[TurnDigest]:
        return list(self._digests.get(session_id, []))
