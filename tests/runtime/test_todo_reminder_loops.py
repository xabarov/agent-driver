"""Tests for periodic todo reminders before LLM calls."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.enums import ChatRole
from agent_driver.runtime.single_agent.todo_reminders import (
    TODO_REMINDER_TOOL_LOOPS,
    maybe_append_todo_reminder_to_protocol,
)


def _context_with_plan(*, loops: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={
            "tool_loops_since_todo_write": loops,
            "planning_state": {
                "run_id": "run_rem",
                "todos": [
                    {
                        "todo_id": "a",
                        "content": "First",
                        "status": "in_progress",
                    },
                    {"todo_id": "b", "content": "Second", "status": "pending"},
                ],
                "metadata": {},
            },
        },
        run_id="run_rem",
    )


def test_reminder_appended_after_threshold_loops() -> None:
    context = _context_with_plan(loops=TODO_REMINDER_TOOL_LOOPS)
    base = (ChatMessage(role=ChatRole.USER, content="hello"),)
    extended = maybe_append_todo_reminder_to_protocol(context, base)
    assert extended is not None
    assert len(extended) == len(base) + 1
    reminder = extended[-1]
    assert "[in_progress]" in reminder.content
    assert "merge=true" in reminder.content


def test_no_reminder_below_threshold() -> None:
    context = _context_with_plan(loops=TODO_REMINDER_TOOL_LOOPS - 1)
    base = (ChatMessage(role=ChatRole.USER, content="hello"),)
    assert maybe_append_todo_reminder_to_protocol(context, base) == base
