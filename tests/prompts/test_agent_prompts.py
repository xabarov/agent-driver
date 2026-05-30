"""Tests for externalized agent prompt templates."""

from __future__ import annotations

from datetime import UTC, datetime

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
    assert datetime.now(UTC).date().isoformat() in chat_policy
    assert "counting/listing questions" in chat_policy
    assert "truncated=true" in chat_policy
    assert "Never emit plain-text `<tool_call>" in chat_policy
    assert "same language in the final answer" in chat_policy
    assert "Do not read local repository files for purely external" in chat_policy
    assert "attempt the appropriate filesystem tool once" in chat_policy
    assert "grep_search for the relevant prefix" in chat_policy
    assert "## Subagent Delegation" in chat_policy
    assert "When `agent_tool` is available" in chat_policy
    assert "Do not use `agent_tool` for simple factual questions" in chat_policy


def test_force_final_answer_templates_are_non_empty() -> None:
    assert force_final_answer_user_message()
    force_answer = force_final_answer_tool_message()
    assert force_answer
    assert "Exception:" in force_answer
    assert "Produce the requested deliverable" in force_answer
    assert "do not only summarize progress" in force_answer
