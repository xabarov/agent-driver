"""Session store protocol for Phase-6 context engineering."""

from __future__ import annotations

from typing import Protocol

from agent_driver.contracts.context import SessionRef, SessionTurn, TurnDigest


class SessionStore(Protocol):
    """Protocol for persisting session turns and digest state."""

    def upsert_session(self, session: SessionRef) -> SessionRef:
        """Create or update session reference metadata."""
        raise NotImplementedError

    def get_session(self, session_id: str) -> SessionRef | None:
        """Load one session reference by identifier."""
        raise NotImplementedError

    def append_turn(self, turn: SessionTurn) -> SessionTurn:
        """Append one session turn."""
        raise NotImplementedError

    def list_turns(self, session_id: str) -> list[SessionTurn]:
        """List turns for one session ordered by turn index."""
        raise NotImplementedError

    def latest_turn(self, session_id: str) -> SessionTurn | None:
        """Return latest turn for one session."""
        raise NotImplementedError

    def save_digest(self, session_id: str, digest: TurnDigest) -> TurnDigest:
        """Persist digest for one session turn."""
        raise NotImplementedError

    def list_digests(self, session_id: str) -> list[TurnDigest]:
        """List digests for one session ordered by turn index."""
        raise NotImplementedError
