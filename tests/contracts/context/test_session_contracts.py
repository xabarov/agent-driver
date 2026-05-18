"""Session contracts tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import ChatMessage, SessionRef, SessionTurn, TurnDigest


def test_session_turn_accepts_digest_payload() -> None:
    """Session turn should carry optional turn digest."""
    turn = SessionTurn(
        session_id="sess_1",
        turn_index=0,
        message=ChatMessage(role="user", content="hello"),
        digest=TurnDigest(digest_id="dig_1", turn_index=0, summary="summary"),
    )
    assert turn.digest is not None
    assert turn.digest.summary == "summary"


def test_session_turn_rejects_negative_turn_index() -> None:
    """Session turn should reject negative turn indexes."""
    with pytest.raises(ValidationError):
        SessionTurn(
            session_id="sess_1",
            turn_index=-1,
            message=ChatMessage(role="user", content="hello"),
        )


def test_session_ref_round_trip_metadata() -> None:
    """Session ref should preserve JSON-safe metadata."""
    ref = SessionRef(
        session_id="sess_1",
        run_id="run_1",
        attempt_id="attempt_1",
        metadata={"workspace": "ws_1"},
    )
    restored = SessionRef.model_validate(ref.model_dump(mode="json"))
    assert restored.metadata["workspace"] == "ws_1"
