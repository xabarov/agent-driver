"""Planning state transitions and helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_driver.contracts.context import PlanningState, PlanningStep, TodoState
from agent_driver.contracts.enums import PlanningTodoStatus


def planning_state_init(run_id: str) -> PlanningState:
    """Create empty planning state for run."""
    return PlanningState(run_id=run_id)


def planning_state_set_step(state: PlanningState, step: PlanningStep) -> PlanningState:
    """Set latest planning step in immutable style."""
    return state.model_copy(
        update={
            "latest_step": step,
            "updated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
    )


def planning_state_upsert_todo(state: PlanningState, todo: TodoState) -> PlanningState:
    """Insert or replace todo by identifier."""
    next_todos: list[TodoState] = []
    replaced = False
    for item in state.todos:
        if item.todo_id == todo.todo_id:
            next_todos.append(todo)
            replaced = True
        else:
            next_todos.append(item)
    if not replaced:
        next_todos.append(todo)
    return state.model_copy(
        update={
            "todos": next_todos,
            "updated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
    )


def planning_state_set_todo_status(
    state: PlanningState, *, todo_id: str, status: PlanningTodoStatus
) -> PlanningState:
    """Set status for one existing todo item."""
    next_todos: list[TodoState] = []
    for item in state.todos:
        if item.todo_id == todo_id:
            next_todos.append(item.model_copy(update={"status": status}))
        else:
            next_todos.append(item)
    return state.model_copy(
        update={
            "todos": next_todos,
            "updated_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        }
    )
