"""Subagent budget and max-child guard tests."""

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


async def _fake_child_runner(run_input):
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
        answer=f"done:{run_input.input}",
    )


@pytest.mark.asyncio
async def test_execute_subagent_group_respects_max_child_runs() -> None:
    """Executor should cap fanout by max_child_runs."""
    store = InMemorySubagentStore()
    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(
            run_id="run_1", attempt_id="att_1", agent_id="agent", answer="parent"
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_1",
            purpose="fanout",
            tasks=tuple(
                SubagentTaskSpec(task_id=f"t{idx}", task=f"q{idx}", description="d")
                for idx in range(5)
            ),
        ),
        store=store,
        child_runner=_fake_child_runner,
        max_child_runs=2,
    )
    assert len(result.runs) == 2
