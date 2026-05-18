"""Planning state transitions and prompt renderer tests."""

from __future__ import annotations

from agent_driver.context.planning import (
    planning_state_event,
    planning_state_init,
    planning_state_set_step,
    planning_state_set_todo_status,
    planning_state_upsert_todo,
    planning_step_event,
    render_planning_step_prompt,
)
from agent_driver.contracts import PlanningStep, PlanningTodoStatus, TodoState


def test_planning_state_transitions_and_prompt() -> None:
    """Planning state should preserve todos and latest step transitions."""
    state = planning_state_init("run_1")
    state = planning_state_upsert_todo(
        state,
        TodoState(todo_id="todo_1", content="Inspect logs"),
    )
    state = planning_state_set_todo_status(
        state, todo_id="todo_1", status=PlanningTodoStatus.IN_PROGRESS
    )
    step = PlanningStep(
        step_id="step_1",
        facts_given=["A"],
        facts_learned=["B"],
        facts_to_lookup=["C"],
        facts_to_derive=["D"],
        next_plan="Execute",
    )
    state = planning_state_set_step(state, step)
    prompt = render_planning_step_prompt(step)
    assert state.todos[0].status == PlanningTodoStatus.IN_PROGRESS
    assert state.latest_step is not None
    assert "Facts Given:" in prompt
    assert "Next Plan:" in prompt


def test_planning_events_use_dedicated_channel() -> None:
    """Planning events should be tagged with planning channel payload."""
    step = PlanningStep(
        step_id="step_2",
        facts_given=[],
        facts_learned=[],
        facts_to_lookup=[],
        facts_to_derive=[],
        next_plan="Proceed",
    )
    state = planning_state_set_step(planning_state_init("run_2"), step)
    event_step = planning_step_event(
        run_id="run_2", attempt_id="attempt_1", seq=1, step=step
    )
    event_state = planning_state_event(
        run_id="run_2", attempt_id="attempt_1", seq=2, state=state
    )
    assert event_step.payload["channel"] == "planning"
    assert event_state.payload["channel"] == "planning"
