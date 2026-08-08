"""Planning P4 — escalate the plan reminder when a step is stuck in_progress."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.context import PlanningState, TodoState
from agent_driver.contracts.enums import PlanningTodoStatus as S
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    format_stale_todo_escalation,
    maybe_append_todo_reminder_to_protocol,
)


def _state(*todos: tuple[str, str, S]) -> PlanningState:
    return PlanningState(
        run_id="r",
        todos=[TodoState(todo_id=i, content=c, status=s) for i, c, s in todos],
    )


def _ctx(state: PlanningState, loops: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={
            "planning_state": state.model_dump(mode="json"),
            "tool_loops_since_todo_write": loops,
        }
    )


# --- helper unit ---------------------------------------------------------------


def test_escalation_names_the_stuck_step() -> None:
    text = format_stale_todo_escalation(
        _state(("a", "build the chart", S.IN_PROGRESS), ("b", "write up", S.PENDING)), 6
    )
    assert "build the chart" in text
    assert "in progress for 6 tool steps" in text
    assert "split it" in text and "cancel it" in text


def test_escalation_empty_without_single_in_progress() -> None:
    assert format_stale_todo_escalation(_state(("a", "x", S.PENDING)), 6) == ""
    assert (
        format_stale_todo_escalation(
            _state(("a", "x", S.IN_PROGRESS), ("b", "y", S.IN_PROGRESS)), 6
        )
        == ""
    )


# --- integration through the reminder ------------------------------------------


def test_reminder_escalates_when_stale() -> None:
    ctx = _ctx(_state(("a", "build the chart", S.IN_PROGRESS)), 6)
    out = maybe_append_todo_reminder_to_protocol(ctx, tuple())
    assert out is not None and len(out) == 1
    content = out[0].content or ""
    assert "Remaining steps:" in content  # the normal reminder is still there
    assert "has been in progress for 6 tool steps" in content  # + escalation


def test_reminder_not_escalated_below_stale_threshold() -> None:
    # >= reminder threshold (2) but < stale threshold (5): normal reminder, no escalation.
    ctx = _ctx(_state(("a", "build the chart", S.IN_PROGRESS)), 3)
    out = maybe_append_todo_reminder_to_protocol(ctx, tuple())
    assert out is not None and len(out) == 1
    content = out[0].content or ""
    assert "Remaining steps:" in content
    assert "in progress for" not in content


def test_no_reminder_below_reminder_threshold() -> None:
    ctx = _ctx(_state(("a", "x", S.IN_PROGRESS)), 1)
    assert maybe_append_todo_reminder_to_protocol(ctx, tuple()) == tuple()
