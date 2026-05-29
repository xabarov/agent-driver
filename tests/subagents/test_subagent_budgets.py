"""Subagent budget and max-child guard tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
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
                context={
                    "run_id": run_input.run_id or "child",
                    "attempt_id": "att_child",
                    "seq": 1,
                },
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


@pytest.mark.asyncio
async def test_execute_subagent_group_respects_max_parallel_backpressure() -> None:
    """Executor should cap scheduling by group max_parallel."""
    store = InMemorySubagentStore()
    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(
            run_id="run_1", attempt_id="att_1", agent_id="agent", answer="parent"
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_1",
            purpose="fanout",
            max_parallel=1,
            tasks=tuple(
                SubagentTaskSpec(task_id=f"t{idx}", task=f"q{idx}", description="d")
                for idx in range(3)
            ),
        ),
        store=store,
        child_runner=_fake_child_runner,
        max_child_runs=4,
    )
    group = store.list_groups("run_1")[0]

    assert len(result.runs) == 1
    assert group.metadata["scheduled_tasks"] == 1
    assert group.metadata["backpressure_skipped_tasks"] == [
        {"task_id": "t1", "reason": "parallel_limit"},
        {"task_id": "t2", "reason": "parallel_limit"},
    ]


@pytest.mark.asyncio
async def test_execute_subagent_group_respects_declared_token_budget() -> None:
    """Executor should skip child tasks that exceed remaining group token budget."""
    store = InMemorySubagentStore()
    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(
            run_id="run_1", attempt_id="att_1", agent_id="agent", answer="parent"
        ),
        group_spec=SubagentGroupSpec(
            group_id="grp_1",
            purpose="fanout",
            token_budget=10,
            tasks=(
                SubagentTaskSpec(
                    task_id="t1",
                    task="q1",
                    description="d",
                    token_budget=6,
                ),
                SubagentTaskSpec(
                    task_id="t2",
                    task="q2",
                    description="d",
                    token_budget=6,
                ),
            ),
        ),
        store=store,
        child_runner=_fake_child_runner,
        max_child_runs=4,
    )
    group = store.list_groups("run_1")[0]

    assert len(result.runs) == 1
    assert group.metadata["token_budget_remaining"] == 4
    assert group.metadata["backpressure_skipped_tasks"] == [
        {"task_id": "t2", "reason": "token_budget"}
    ]
