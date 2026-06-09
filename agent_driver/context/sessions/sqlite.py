"""SQLite-backed session store implementation."""

from __future__ import annotations

from agent_driver.context.sessions.protocols import SessionStore
from agent_driver.contracts.context import SessionRef, SessionTurn, TurnDigest
from agent_driver.persistence import SqliteStoreBase


class SqliteSessionStore(SqliteStoreBase, SessionStore):
    """SQLite session store with turns and digest persistence."""

    def _init_schema(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS session_turns (
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (session_id, turn_index)
            )
            """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS session_digests (
                session_id TEXT NOT NULL,
                digest_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (session_id, digest_id)
            )
            """)

    def upsert_session(self, session: SessionRef) -> SessionRef:
        self._execute(
            """
            INSERT OR REPLACE INTO sessions (session_id, payload)
            VALUES (?, ?)
            """,
            (session.session_id, session.model_dump_json()),
        )
        return session

    def get_session(self, session_id: str) -> SessionRef | None:
        rows = self._query(
            "SELECT payload FROM sessions WHERE session_id = ?", (session_id,)
        )
        if not rows:
            return None
        return SessionRef.model_validate_json(rows[0][0])

    def append_turn(self, turn: SessionTurn) -> SessionTurn:
        self._execute(
            """
            INSERT OR REPLACE INTO session_turns (session_id, turn_index, payload)
            VALUES (?, ?, ?)
            """,
            (turn.session_id, turn.turn_index, turn.model_dump_json()),
        )
        return turn

    def list_turns(self, session_id: str) -> list[SessionTurn]:
        rows = self._query(
            """
            SELECT payload FROM session_turns
            WHERE session_id = ?
            ORDER BY turn_index ASC
            """,
            (session_id,),
        )
        return [SessionTurn.model_validate_json(payload) for (payload,) in rows]

    def latest_turn(self, session_id: str) -> SessionTurn | None:
        rows = self._query(
            """
            SELECT payload FROM session_turns
            WHERE session_id = ?
            ORDER BY turn_index DESC
            LIMIT 1
            """,
            (session_id,),
        )
        if not rows:
            return None
        return SessionTurn.model_validate_json(rows[0][0])

    def save_digest(self, session_id: str, digest: TurnDigest) -> TurnDigest:
        self._execute(
            """
            INSERT OR REPLACE INTO session_digests (
                session_id, digest_id, turn_index, payload
            )
            VALUES (?, ?, ?, ?)
            """,
            (session_id, digest.digest_id, digest.turn_index, digest.model_dump_json()),
        )
        return digest

    def list_digests(self, session_id: str) -> list[TurnDigest]:
        rows = self._query(
            """
            SELECT payload FROM session_digests
            WHERE session_id = ?
            ORDER BY turn_index ASC
            """,
            (session_id,),
        )
        return [TurnDigest.model_validate_json(payload) for (payload,) in rows]
