"""Epic 043 B: single pre-send owner pads only empty non-final turns."""

from __future__ import annotations

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.message_hygiene import repair_empty_non_final_messages


def _req(*messages: ChatMessage) -> LlmRequest:
    return LlmRequest(messages=list(messages), model="fake")


def test_pads_empty_non_final_user_turn() -> None:
    req = _req(
        ChatMessage(role=ChatRole.USER, content=""),
        ChatMessage(role=ChatRole.ASSISTANT, content="hi"),
    )
    out = repair_empty_non_final_messages(req)
    assert out is not req
    assert out.messages[0].content == "."
    assert out.messages[0].metadata["empty_non_final_repaired"] is True
    # Final turn untouched.
    assert out.messages[1].content == "hi"


def test_final_empty_turn_is_never_padded() -> None:
    req = _req(
        ChatMessage(role=ChatRole.USER, content="q"),
        ChatMessage(role=ChatRole.ASSISTANT, content=""),
    )
    out = repair_empty_non_final_messages(req)
    assert out is req  # no change → same object


def test_designed_empty_assistant_tool_call_carrier_untouched() -> None:
    req = _req(
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content="",
            metadata={"tool_calls": [{"id": "c1", "type": "function"}]},
        ),
        ChatMessage(role=ChatRole.TOOL, content="{}", tool_call_id="c1"),
        ChatMessage(role=ChatRole.USER, content="next"),
    )
    out = repair_empty_non_final_messages(req)
    assert out is req  # tool-call carrier + tool row both count as payload


def test_empty_tool_row_is_not_padded() -> None:
    req = _req(
        ChatMessage(role=ChatRole.TOOL, content="", tool_call_id="c1"),
        ChatMessage(role=ChatRole.USER, content="next"),
    )
    out = repair_empty_non_final_messages(req)
    assert out is req  # tool rows are legitimately terse, never padded


def test_idempotent() -> None:
    req = _req(
        ChatMessage(role=ChatRole.USER, content=""),
        ChatMessage(role=ChatRole.ASSISTANT, content="a"),
    )
    once = repair_empty_non_final_messages(req)
    twice = repair_empty_non_final_messages(once)
    assert twice is once  # second pass finds nothing to change


def test_reasoning_echo_carrier_untouched() -> None:
    req = _req(
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content="",
            metadata={"reasoning_details": [{"type": "reasoning.text"}]},
        ),
        ChatMessage(role=ChatRole.USER, content="q"),
    )
    out = repair_empty_non_final_messages(req)
    assert out is req


def test_single_message_request_untouched() -> None:
    req = _req(ChatMessage(role=ChatRole.USER, content=""))
    out = repair_empty_non_final_messages(req)
    assert out is req  # a lone (final) turn is never padded
