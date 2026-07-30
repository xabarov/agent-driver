"""Epic 043 D: inline-CoT quarantine detector for the empty-response ladder."""

from __future__ import annotations

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.message_hygiene import quarantine_inline_reasoning


def _req(*messages: ChatMessage) -> LlmRequest:
    return LlmRequest(messages=list(messages), model="fake")


def test_detects_and_sanitizes_assistant_cot() -> None:
    req = _req(
        ChatMessage(role=ChatRole.USER, content="q"),
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content="<think>secret plan</think>Here is the answer.",
        ),
        ChatMessage(role=ChatRole.USER, content="continue"),
    )
    out, count = quarantine_inline_reasoning(req)
    assert count == 1
    assert out is not req
    assert out.messages[1].content == "Here is the answer."
    # Non-assistant turns untouched.
    assert out.messages[0].content == "q"


def test_counts_multiple_poisoned_turns() -> None:
    req = _req(
        ChatMessage(role=ChatRole.ASSISTANT, content="<think>a</think>one"),
        ChatMessage(role=ChatRole.USER, content="x"),
        ChatMessage(role=ChatRole.ASSISTANT, content="<think>b</think>two"),
    )
    out, count = quarantine_inline_reasoning(req)
    assert count == 2
    assert out.messages[0].content == "one"
    assert out.messages[2].content == "two"


def test_clean_history_is_a_noop() -> None:
    req = _req(
        ChatMessage(role=ChatRole.USER, content="q"),
        ChatMessage(role=ChatRole.ASSISTANT, content="plain answer"),
    )
    out, count = quarantine_inline_reasoning(req)
    assert count == 0
    assert out is req  # unchanged → same object, no wasted retry


def test_user_turn_with_think_tag_is_not_quarantined() -> None:
    # Only assistant turns expose CoT; a user quoting <think> is left alone.
    req = _req(
        ChatMessage(role=ChatRole.USER, content="<think>not mine</think>hi"),
        ChatMessage(role=ChatRole.ASSISTANT, content="answer"),
    )
    out, count = quarantine_inline_reasoning(req)
    assert count == 0
    assert out is req
