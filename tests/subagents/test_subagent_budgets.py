"""Config-level child budgets + structured exhaustion marker (epic 019 phase C)."""

from __future__ import annotations

from agent_driver.contracts import AgentRunOutput, RunStatus, TerminalReason
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.runtime.single_agent.lifecycle.config_sections import SubagentSettings
from agent_driver.runtime.single_agent.tool_stage.subagent_execution import (
    _group_spec_from_planned,
    _stamp_child_budget_defaults,
)
from agent_driver.subagents.executor import _child_budget_summary
from agent_driver.subagents.specs import SubagentTaskSpec


def _planned(task_metadata: dict | None = None) -> dict:
    return {
        "group_id": "g1",
        "tasks": [
            {
                "task_id": "t1",
                "task": "find things",
                "description": "child",
                **({"metadata": task_metadata} if task_metadata else {}),
            }
        ],
    }


def test_stamp_child_budget_defaults_applies_config():
    settings = SubagentSettings(
        default_child_max_steps=10, default_child_max_tool_calls=4
    )
    group = _group_spec_from_planned(_planned(), max_child_runs=4)
    stamped = _stamp_child_budget_defaults(group, settings)
    metadata = stamped.tasks[0].metadata
    assert metadata["max_steps"] == 10
    assert metadata["max_tool_calls"] == 4


def test_stamp_respects_explicit_task_budgets():
    settings = SubagentSettings(
        default_child_max_steps=10, default_child_max_tool_calls=4
    )
    group = _group_spec_from_planned(
        _planned({"max_steps": 3, "max_tool_calls": 2}), max_child_runs=4
    )
    stamped = _stamp_child_budget_defaults(group, settings)
    metadata = stamped.tasks[0].metadata
    assert metadata["max_steps"] == 3  # planner's own value wins
    assert metadata["max_tool_calls"] == 2


def test_stamp_noop_without_config_defaults():
    group = _group_spec_from_planned(_planned(), max_child_runs=4)
    assert _stamp_child_budget_defaults(group, SubagentSettings()) is group


def test_child_budget_summary_marks_exhaustion():
    task = SubagentTaskSpec(
        task_id="t1", task="x", description="d", metadata={"max_steps": 5}
    )
    exhausted = AgentRunOutput(
        run_id="child_1",
        attempt_id="att_1",
        status=RunStatus.TIMED_OUT,
        answer="partial",
        terminal_reason=TerminalReason.MAX_STEPS_EXCEEDED,
        events=[
            RuntimeEvent(
                event_id="ev1",
                type="run_failed",
                run_id="child_1",
                attempt_id="att_1",
                seq=1,
                created_at="2026-07-19T00:00:00Z",
            )
        ],
    )
    summary = _child_budget_summary(task, exhausted)
    assert summary["budget_exhausted"] is True
    assert summary["terminal_reason"] == "max_steps_exceeded"
    assert summary["max_steps"] == 5

    finished = AgentRunOutput(
        run_id="child_2",
        attempt_id="att_2",
        status=RunStatus.COMPLETED,
        answer="done",
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            RuntimeEvent(
                event_id="ev2",
                type="run_completed",
                run_id="child_2",
                attempt_id="att_2",
                seq=1,
                created_at="2026-07-19T00:00:00Z",
            )
        ],
    )
    assert _child_budget_summary(task, finished)["budget_exhausted"] is False
