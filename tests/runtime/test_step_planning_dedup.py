"""Tests for planning-step dedup behavior."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.enums import ToolPolicyDecision
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.runtime.single_agent.step_planning import (
    apply_planning_updates_from_envelopes,
)
from agent_driver.runtime.tools import ToolExecutionResult


def _todo_envelope(todo_id: str, content: str, status: str) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        call=ToolCall(tool_name="todo_write", args={"merge": False}),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={
            "summary": "todo_write applied 1 rows",
            "applied_args": {
                "todo_items": [{"id": todo_id, "content": content, "status": status}],
                "todo_merge": False,
            },
        },
    )


def test_apply_planning_updates_dedups_repeated_todo_write_payload() -> None:
    context = SimpleNamespace(
        metadata={},
        run_id="run_dedup",
    )
    first = ToolExecutionResult(envelopes=[_todo_envelope("1", "task", "in_progress")])
    second = ToolExecutionResult(envelopes=[_todo_envelope("1", "task", "in_progress")])

    updated_first = apply_planning_updates_from_envelopes(context, first)
    updated_second = apply_planning_updates_from_envelopes(context, second)

    assert updated_first is True
    assert updated_second is False
    assert context.metadata.get("todo_write_deduped") is True
    assert "duplicate payload ignored" in str(second.envelopes[0].summary)
