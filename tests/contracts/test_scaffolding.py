"""Epic 043 C: one scaffolding tag, honored by every layer's single predicate."""

from __future__ import annotations

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.scaffolding import (
    SCAFFOLDING_METADATA_KEY,
    is_scaffolding,
    scaffolding_kind,
    scaffolding_metadata,
)


def test_metadata_helper_tags_and_preserves_base_and_extra() -> None:
    meta = scaffolding_metadata("todo_reminder", base={"kind": "todo_reminder"}, seq=3)
    assert meta[SCAFFOLDING_METADATA_KEY] == "todo_reminder"
    assert meta["kind"] == "todo_reminder"  # base preserved
    assert meta["seq"] == 3  # extra merged


def test_predicate_accepts_chatmessage() -> None:
    msg = ChatMessage(
        role=ChatRole.USER,
        content="nudge",
        metadata=scaffolding_metadata("denial_recovery"),
    )
    assert is_scaffolding(msg) is True
    assert scaffolding_kind(msg) == "denial_recovery"


def test_predicate_accepts_serialized_message_dict() -> None:
    msg = ChatMessage(
        role=ChatRole.USER,
        content="nudge",
        metadata=scaffolding_metadata("unknown_tool_recovery"),
    )
    row = msg.model_dump(mode="json")
    assert is_scaffolding(row) is True
    assert scaffolding_kind(row) == "unknown_tool_recovery"


def test_predicate_accepts_bare_metadata_mapping() -> None:
    assert is_scaffolding(scaffolding_metadata("force_final_answer")) is True


def test_genuine_user_turn_is_not_scaffolding() -> None:
    msg = ChatMessage(role=ChatRole.USER, content="real question")
    assert is_scaffolding(msg) is False
    assert scaffolding_kind(msg) is None
    assert is_scaffolding(msg.model_dump(mode="json")) is False


def test_empty_kind_is_not_scaffolding() -> None:
    assert is_scaffolding({"metadata": {SCAFFOLDING_METADATA_KEY: ""}}) is False
    assert is_scaffolding(None) is False
