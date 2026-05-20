"""Tests for externalized agent prompt templates."""

from __future__ import annotations

from agent_driver.prompts import (
    force_final_answer_tool_message,
    force_final_answer_user_message,
    react_base_policy,
    react_chat_tool_policy,
)


def test_react_prompt_templates_are_non_empty() -> None:
    assert react_base_policy()
    chat_policy = react_chat_tool_policy()
    assert chat_policy
    assert "counting/listing questions" in chat_policy
    assert "truncated=true" in chat_policy


def test_force_final_answer_templates_are_non_empty() -> None:
    assert force_final_answer_user_message()
    force_answer = force_final_answer_tool_message()
    assert force_answer
    assert "Exception:" in force_answer
