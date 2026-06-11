"""Compatibility shim for todo reminder helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    SUBSTANTIVE_TODO_HINT_TOOLS,
    TODO_REMINDER_TOOL_LOOPS,
    active_in_progress_todo,
    append_todo_progress_hint_after_substantive_tool,
    format_todo_list_reminder,
    has_unfinished_todos,
    increment_tool_loops_since_todo_write,
    maybe_append_todo_reminder_to_protocol,
    planning_state_from_metadata,
    reset_todo_write_loop_counters,
    unfinished_todos,
)

__all__ = [
    "SUBSTANTIVE_TODO_HINT_TOOLS",
    "TODO_REMINDER_TOOL_LOOPS",
    "active_in_progress_todo",
    "append_todo_progress_hint_after_substantive_tool",
    "format_todo_list_reminder",
    "has_unfinished_todos",
    "increment_tool_loops_since_todo_write",
    "maybe_append_todo_reminder_to_protocol",
    "planning_state_from_metadata",
    "reset_todo_write_loop_counters",
    "unfinished_todos",
]
