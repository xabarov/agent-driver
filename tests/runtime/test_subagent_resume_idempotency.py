"""Subagent idempotency tests across repeated execution."""

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


async def _child_runner(run_input):
    return AgentRunOutput(
        run_id=run_input.run_id or "child",
        attempt_id="att",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": run_input.run_id or "child", "attempt_id": "att", "seq": 1},
            )
        ],
        answer=f"ok:{run_input.input}",
    )


@pytest.mark.asyncio
async def test_subagent_reexecution_with_same_idempotency_key_does_not_duplicate() -> None:
    """Re-running same group should not duplicate child rows."""
    store = InMemorySubagentStore()
    spec = SubagentGroupSpec(
        group_id="grp_1",
        purpose="fanout",
        tasks=(
            SubagentTaskSpec(
                task_id="task_1",
                task="work",
                description="d",
                idempotency_key="same-key",
            ),
        ),
    )
    parent = default_parent_handoff(
        run_id="run_1", attempt_id="att_1", agent_id="agent"
    )
    await execute_subagent_group_sync(
        parent=parent,
        group_spec=spec,
        store=store,
        child_runner=_child_runner,
        max_child_runs=4,
    )
    await execute_subagent_group_sync(
        parent=parent,
        group_spec=spec,
        store=store,
        child_runner=_child_runner,
        max_child_runs=4,
    )
    assert len(store.list_runs("run_1")) == 1
