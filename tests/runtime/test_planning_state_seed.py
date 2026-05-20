"""Tests for planning state seed across chat turns."""

from __future__ import annotations

from agent_driver.contracts import AgentRunInput
from agent_driver.runtime.single_agent.step_planning import (
    apply_planning_state_seed_from_metadata,
    build_planning_snapshot,
)
from agent_driver.runtime.single_agent.types import RunContext


def test_apply_planning_state_seed_from_metadata() -> None:
    run_input = AgentRunInput(
        input="hello",
        run_id="run_seed_test",
        agent_id="agent.test",
        graph_preset="single_react",
    )
    context = RunContext(
        run_input=run_input,
        identifiers={"run_id": "run_seed_test", "attempt_id": "attempt_1"},
        metadata={
            "planning_state_seed": {
                "todos": [
                    {"id": "s1", "content": "First", "status": "completed"},
                    {"id": "s2", "content": "Second", "status": "in_progress"},
                ],
                "completed": 1,
                "total": 2,
            }
        },
    )
    apply_planning_state_seed_from_metadata(context)
    assert "planning_state_seed" not in context.metadata
    state = context.metadata.get("planning_state")
    assert isinstance(state, dict)
    todos = state.get("todos")
    assert isinstance(todos, list) and len(todos) == 2
    snapshot = build_planning_snapshot(context)
    assert snapshot is not None
    assert snapshot["completed"] == 1
    assert snapshot["total"] == 2
