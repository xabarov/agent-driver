"""Tests for todo_write structured tool results."""

from __future__ import annotations

import pytest

from agent_driver.tools.planning import (
    build_todo_write_summary_and_next_action,
    _todo_write_tool,
)


def test_build_summary_in_progress() -> None:
    todos = [
        {"id": "1", "content": "Search", "status": "completed"},
        {"id": "2", "content": "Write", "status": "in_progress"},
        {"id": "3", "content": "Review", "status": "pending"},
    ]
    summary, next_action = build_todo_write_summary_and_next_action(todos)
    assert "1/3 done" in summary
    assert "in_progress=2" in summary
    assert "do not repeat" in summary.lower()
    assert "merge=true" in next_action
    assert "2" in next_action


def test_build_summary_all_completed() -> None:
    todos = [{"id": "1", "content": "Only", "status": "completed"}]
    summary, next_action = build_todo_write_summary_and_next_action(todos)
    assert "All steps done" in summary
    assert "completed" in next_action.lower()


@pytest.mark.asyncio
async def test_todo_write_tool_returns_structured_fields() -> None:
    result = await _todo_write_tool(
        {
            "merge": True,
            "todos": [
                {"id": "s1", "content": "Step one", "status": "in_progress"},
            ],
        }
    )
    assert "current_todos" in result
    structured = result.get("structured")
    assert isinstance(structured, dict)
    assert structured["merge"] is True
    assert structured["next_action"]
    assert result["summary"]
