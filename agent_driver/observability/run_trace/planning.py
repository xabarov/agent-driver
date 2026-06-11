"""Planning analyzers for run trace summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.tools import event_data
from agent_driver.runtime.planning_check import (
    EXIT_PLAN_MODE_TOOL_NAMES,
    PLANNING_TOOL_NAMES,
)
from agent_driver.runtime.research_session_contract import unfinished_todo_labels


def is_plan_only_prompt(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "только план",
            "только план поиска",
            "без реферата",
            "без черновика",
            "plan only",
            "only plan",
            "just the plan",
            "no report",
            "without writing",
        )
    )


def planning_summary(
    events: list[dict[str, object]],
    tool_names: list[str],
) -> dict[str, Any]:
    planning_tool_count = sum(1 for name in tool_names if name in PLANNING_TOOL_NAMES)
    enter_plan_count = tool_names.count("enter_plan_mode")
    exit_plan_count = sum(1 for name in tool_names if name in EXIT_PLAN_MODE_TOOL_NAMES)
    data_tool_count = sum(1 for name in tool_names if name not in PLANNING_TOOL_NAMES)
    snapshots = 0
    latest_snapshot: dict[str, Any] | None = None
    for event in events:
        snapshot = event_data(event).get("planning_snapshot")
        if isinstance(snapshot, dict):
            snapshots += 1
            latest_snapshot = dict(snapshot)
    if planning_tool_count == 0:
        verdict = None
    else:
        verdict = "engaged" if data_tool_count > 0 else "fabricated"
    return {
        "verdict": verdict,
        "planning_tool_calls": planning_tool_count,
        "approval_cycles": min(enter_plan_count, exit_plan_count),
        "enter_plan_mode_calls": enter_plan_count,
        "exit_plan_mode_calls": exit_plan_count,
        "data_tool_calls": data_tool_count,
        "snapshots": snapshots,
        "latest_snapshot": latest_snapshot,
    }


def planning_todos_incomplete(
    planning: dict[str, Any],
    *,
    assistant_text: str = "",
    allow_all_todos: bool = False,
) -> bool:
    if allow_all_todos:
        return False
    latest = planning.get("latest_snapshot")
    if not isinstance(latest, dict):
        return False
    todos = latest.get("todos")
    if not isinstance(todos, list):
        return False
    normalized_todos: list[dict[str, Any]] = []
    for todo in todos:
        if not isinstance(todo, dict):
            continue
        item = dict(todo)
        if "todo_id" not in item and "id" in item:
            item["todo_id"] = item.pop("id")
        normalized_todos.append(item)
    planning_state = {
        "run_id": "trace_summary",
        "todos": normalized_todos,
        "metadata": {},
    }
    return bool(unfinished_todo_labels(planning_state, assistant_text=assistant_text))


def planning_execution_expected(
    *,
    requires_research: bool,
    user_prompt: str | None,
    assistant_text: str,
) -> bool:
    if requires_research:
        return True
    prompt = " ".join((user_prompt or "").lower().split())
    if any(marker in prompt for marker in ("выполни", "execute", "implement", "fix")):
        return True
    answer = assistant_text.lower()
    return any(
        marker in answer
        for marker in (
            "данные собраны",
            "источники изучены",
            "были выполнены",
            "проведён поиск",
            "проведен поиск",
            "research completed",
            "data collected",
        )
    )


__all__ = [
    "is_plan_only_prompt",
    "planning_execution_expected",
    "planning_summary",
    "planning_todos_incomplete",
]
