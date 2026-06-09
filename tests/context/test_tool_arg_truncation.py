"""E5: deterministic tool-call argument truncation pre-pass."""

from __future__ import annotations

import pytest

from agent_driver.context import truncate_tool_call_args
from agent_driver.contracts.messages import ChatMessage, ChatRole


def _assistant_with_call(args: dict, *, tool_name: str = "write_file") -> ChatMessage:
    return ChatMessage(
        role=ChatRole.ASSISTANT,
        content="",
        metadata={"tool_calls": [{"tool_name": tool_name, "args": args}]},
    )


def test_clips_oversized_string_args_in_older_messages() -> None:
    big = "X" * 5000
    messages = [
        _assistant_with_call({"path": "a.txt", "content": big}),
        ChatMessage(role=ChatRole.USER, content="next"),
        ChatMessage(role=ChatRole.USER, content="tail"),
    ]
    result = truncate_tool_call_args(messages, max_arg_chars=100, protect_last=2)
    assert result.changed
    clipped = result.messages[0].metadata["tool_calls"][0]["args"]["content"]
    assert len(clipped) < 5000 and "arg truncated" in clipped
    # Small args untouched.
    assert result.messages[0].metadata["tool_calls"][0]["args"]["path"] == "a.txt"
    assert result.chars_saved == 5000 - 100
    assert result.audit[0]["tool_name"] == "write_file"
    assert result.audit[0]["arg"] == "content"


def test_protects_last_n_messages() -> None:
    big = "Y" * 5000
    messages = [
        _assistant_with_call({"content": big}),
        _assistant_with_call({"content": big}),
    ]
    # Both messages are within the protected tail → untouched.
    result = truncate_tool_call_args(messages, max_arg_chars=100, protect_last=2)
    assert not result.changed
    assert result.messages == messages


def test_no_tool_calls_is_noop() -> None:
    messages = [
        ChatMessage(role=ChatRole.USER, content="hi"),
        ChatMessage(role=ChatRole.ASSISTANT, content="ok"),
        ChatMessage(role=ChatRole.USER, content="tail"),
    ]
    result = truncate_tool_call_args(messages, max_arg_chars=10, protect_last=1)
    assert not result.changed


def test_does_not_mutate_input() -> None:
    big = "Z" * 3000
    original = _assistant_with_call({"content": big})
    messages = [original, ChatMessage(role=ChatRole.USER, content="t")]
    truncate_tool_call_args(messages, max_arg_chars=50, protect_last=1)
    assert original.metadata["tool_calls"][0]["args"]["content"] == big


def test_negative_max_rejected() -> None:
    with pytest.raises(ValueError):
        truncate_tool_call_args([], max_arg_chars=-1)


def test_non_string_args_skipped() -> None:
    messages = [
        _assistant_with_call({"count": 999999, "flag": True}),
        ChatMessage(role=ChatRole.USER, content="t"),
    ]
    result = truncate_tool_call_args(messages, max_arg_chars=1, protect_last=1)
    assert not result.changed  # non-string args are not clipped
