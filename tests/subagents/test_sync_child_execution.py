"""Sync child execution tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunOutput, RunStatus, RuntimeEventType, TerminalReason, new_runtime_event
from agent_driver.subagents import (
    InMemorySubagentStore,
    SubagentGroupSpec,
    SubagentTaskSpec,
    execute_subagent_group_sync,
)

from tests.subagents.parent_handoff import default_parent_handoff


async def _ok_child_runner(run_input):
    return AgentRunOutput(
        run_id=run_input.run_id or "child",
        attempt_id="att_child",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": run_input.run_id or "child", "attempt_id": "att_child", "seq": 1},
            )
        ],
        answer="child answer",
    )


@pytest.mark.asyncio
async def test_sync_child_execution_records_group_and_runs() -> None:
    """Executor should persist group and child runs."""
    store = InMemorySubagentStore()
    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(task_id="task_1", task="investigate", description="desc"),
            ),
        ),
        store=store,
        child_runner=_ok_child_runner,
        max_child_runs=4,
    )
    assert result.group.group_id == "grp_parent"
    assert result.join_state in {"joined", "race_won", "partial_joined"}
    assert len(result.runs) == 1
    assert result.runs[0].status.value == "completed"
