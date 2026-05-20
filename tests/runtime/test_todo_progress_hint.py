"""Tests for post-substantive-tool todo progress hints."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.enums import ChatRole, ToolPolicyDecision
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.runtime.single_agent.todo_reminders import (
    append_todo_progress_hint_after_substantive_tool,
)
from agent_driver.runtime.tools import ToolExecutionResult


def _context_with_active_plan() -> SimpleNamespace:
    return SimpleNamespace(
        metadata={
            "planning_state": {
                "run_id": "run_hint",
                "todos": [
                    {
                        "todo_id": "step1",
                        "content": "Search sources",
                        "status": "in_progress",
                    },
                    {
                        "todo_id": "step2",
                        "content": "Summarize",
                        "status": "pending",
                    },
                ],
                "metadata": {},
            }
        },
        run_id="run_hint",
    )


def test_hint_after_web_fetch_with_active_in_progress() -> None:
    context = _context_with_active_plan()
    result = ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="web_fetch", args={"url": "https://example.com"}),
                decision=ToolPolicyDecision.ALLOW,
                summary="fetched",
            )
        ]
    )
    messages: list[ChatMessage] = []
    append_todo_progress_hint_after_substantive_tool(context, result, messages)
    assert len(messages) == 1
    assert messages[0].role == ChatRole.USER
    assert "todo_write" in messages[0].content
    assert "step1" in messages[0].content
    assert context.metadata.get("todo_hint_count_step1") == 1


def test_no_hint_after_todo_write() -> None:
    context = _context_with_active_plan()
    result = ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="todo_write", args={}),
                decision=ToolPolicyDecision.ALLOW,
                summary="updated",
            )
        ]
    )
    messages: list[ChatMessage] = []
    append_todo_progress_hint_after_substantive_tool(context, result, messages)
    assert messages == []


def test_hint_limited_to_two_per_in_progress_step() -> None:
    context = _context_with_active_plan()
    context.metadata["todo_hint_count_step1"] = 2
    result = ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="web_search", args={"query": "test"}),
                decision=ToolPolicyDecision.ALLOW,
                summary="ok",
            )
        ]
    )
    messages: list[ChatMessage] = []
    append_todo_progress_hint_after_substantive_tool(context, result, messages)
    assert messages == []
