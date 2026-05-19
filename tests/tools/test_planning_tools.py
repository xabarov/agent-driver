"""Tests for planning/todo/ask-user built-in tools."""

from __future__ import annotations

import pytest

from agent_driver.tools.planning import (
    apply_planning_state_tool_update,
    register_planning_tool,
)
from agent_driver.tools.registry import ToolRegistry
from agent_driver.context import planning_state_init


@pytest.mark.asyncio
async def test_todo_write_validates_single_in_progress_and_returns_applied_args() -> None:
    """todo_write should enforce one in_progress item and return normalized args."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("todo_write")
    assert tool is not None
    out = await tool.handler(
        {
            "merge": False,
            "todos": [
                {"id": "t1", "content": "step 1", "status": "in_progress"},
                {"id": "t2", "content": "step 2", "status": "pending"},
            ],
        }
    )
    applied = out["applied_args"]
    assert applied["todo_merge"] is False
    assert len(applied["todo_items"]) == 2


@pytest.mark.asyncio
async def test_ask_user_question_returns_interrupt_payload_shape() -> None:
    """ask_user_question should return prompt and normalized choices."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("ask_user_question")
    assert tool is not None
    out = await tool.handler(
        {
            "prompt": "Choose mode",
            "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "allow_multiple": False,
        }
    )
    assert out["interrupt_reason"] == "clarification_required"
    assert out["prompt"] == "Choose mode"
    assert len(out["choices"]) == 2


def test_apply_planning_state_tool_update_applies_todo_items_and_mode() -> None:
    """planning helper should apply todo_items and planning_mode."""
    state = planning_state_init("run_plan_tools")
    updated = apply_planning_state_tool_update(
        state,
        {
            "todo_items": [
                {"id": "t1", "content": "a", "status": "pending"},
                {"id": "t2", "content": "b", "status": "completed"},
            ],
            "todo_merge": False,
            "planning_mode": "plan",
        },
    )
    assert len(updated.todos) == 2
    assert updated.metadata["planning_mode"] == "plan"
