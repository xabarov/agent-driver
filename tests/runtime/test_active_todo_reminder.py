"""Planning P3 — the periodic plan reminder re-lists ACTIVE todos only.

Re-listing completed steps after a context compaction is a known way to make a model
re-do finished work (the todo list persists in metadata while the message history that
recorded the work is summarized away), so completed/cancelled steps are collapsed into a
"do NOT redo" note instead of a re-executable line.
"""

from __future__ import annotations

from agent_driver.contracts.context import PlanningState, TodoState
from agent_driver.contracts.enums import PlanningTodoStatus as S
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    format_todo_list_reminder,
)


def _state(*todos: tuple[str, str, S]) -> PlanningState:
    return PlanningState(
        run_id="r",
        todos=[TodoState(todo_id=i, content=c, status=s) for i, c, s in todos],
    )


def test_only_active_steps_are_listed() -> None:
    text = format_todo_list_reminder(
        _state(
            ("a", "download data", S.COMPLETED),
            ("b", "clean data", S.CANCELLED),
            ("c", "build chart", S.IN_PROGRESS),
            ("d", "write summary", S.PENDING),
        )
    )
    # active items present
    assert "[in_progress] build chart" in text
    assert "[pending] write summary" in text
    # completed/cancelled content NOT re-listed as executable steps
    assert "download data" not in text
    assert "clean data" not in text
    # but the model is told how many are done (progress) and not to redo them
    assert "2 of 4 steps are already completed/cancelled" in text
    assert "do NOT redo" in text


def test_all_done_tells_model_to_finalize() -> None:
    text = format_todo_list_reminder(
        _state(("a", "x", S.COMPLETED), ("b", "y", S.COMPLETED))
    )
    assert "All planned steps are done" in text
    assert "final answer" in text
    assert "Remaining steps" not in text


def test_all_active_no_done_note() -> None:
    # Mirrors the existing reminder-loop test scenario.
    text = format_todo_list_reminder(
        _state(("a", "First", S.IN_PROGRESS), ("b", "Second", S.PENDING))
    )
    assert "[in_progress] First" in text
    assert "[pending] Second" in text
    assert "merge=true" in text
    assert "already completed" not in text
