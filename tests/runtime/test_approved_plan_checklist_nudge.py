"""Planning P6 — an approved plan is connected to the todo checklist surface.

After a plan is approved (enter_plan_mode/exit_plan_mode_v2 → approval interrupt →
resume), the approved plan is prose with no todos. The planning_mode_exit reminder now
nudges the model to lay the approved plan out as a todo_write checklist when none exists,
so the plan and the working checklist are the same thing.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.llm_step.prompt import (
    chat_mode_runtime_reminders,
)

_CHECKLIST = "Lay it out now as a todo_write checklist"
_CONTINUE = "continue execution instead of creating"


def _ctx(
    *, approved: bool, todos=None, denied=None, allowed=None
) -> SimpleNamespace:
    metadata: dict = {}
    if approved:
        metadata["approved_plan"] = {"plan_id": "p1"}
    if todos is not None:
        metadata["planning_state"] = {"run_id": "r", "todos": todos}
    return SimpleNamespace(
        metadata=metadata,
        run_input=SimpleNamespace(
            tool_policy=SimpleNamespace(
                metadata={}, allowed_tools=allowed, denied_tools=denied
            ),
            app_metadata={},
            input="сделай задачу",
        ),
    )


def _has(reminders: list[str], text: str) -> bool:
    return any(text in r for r in reminders)


def test_approved_plan_without_todos_nudges_checklist() -> None:
    reminders = chat_mode_runtime_reminders(_ctx(approved=True))
    assert _has(reminders, _CHECKLIST)
    assert not _has(reminders, _CONTINUE)


def test_approved_plan_with_todos_continues() -> None:
    reminders = chat_mode_runtime_reminders(
        _ctx(approved=True, todos=[{"todo_id": "a", "content": "x", "status": "in_progress"}])
    )
    assert _has(reminders, _CONTINUE)
    assert not _has(reminders, _CHECKLIST)


def test_no_checklist_nudge_when_todo_write_denied() -> None:
    reminders = chat_mode_runtime_reminders(
        _ctx(approved=True, denied=["todo_write"])
    )
    assert _has(reminders, _CONTINUE)
    assert not _has(reminders, _CHECKLIST)


def test_no_planning_mode_exit_without_approved_plan() -> None:
    reminders = chat_mode_runtime_reminders(_ctx(approved=False))
    assert not _has(reminders, "planning_mode_exit")
