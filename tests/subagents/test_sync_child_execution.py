"""Sync child execution tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.runtime.abort import RunAbortHandle
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


@pytest.mark.asyncio
async def test_sync_child_execution_passes_abort_handle_to_child_runner() -> None:
    """Executor should pass a cascading child abort handle when supported."""
    store = InMemorySubagentStore()
    parent_abort_handle = RunAbortHandle()
    seen = {}

    async def _runner(run_input, *, abort_handle=None):
        seen["run_id"] = run_input.run_id
        seen["abort_handle"] = abort_handle
        return await _ok_child_runner(run_input)

    await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
        parent_abort_handle=parent_abort_handle,
    )

    assert seen["abort_handle"] is not None
    assert seen["abort_handle"].is_aborted is False
    parent_abort_handle.abort("stop")
    assert seen["abort_handle"].is_aborted is True


@pytest.mark.asyncio
async def test_sync_child_execution_skips_child_when_parent_already_aborted() -> None:
    """Pre-aborted parent should persist cancelled child rows without calling child."""
    store = InMemorySubagentStore()
    parent_abort_handle = RunAbortHandle()
    parent_abort_handle.abort("operator_stop")
    called = False

    async def _runner(_run_input):
        nonlocal called
        called = True
        raise AssertionError("child runner should not be called")

    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent summary"),
        group_spec=SubagentGroupSpec(
            group_id="grp_parent",
            purpose="analysis",
            tasks=(
                SubagentTaskSpec(
                    task_id="task_1",
                    task="investigate",
                    description="desc",
                ),
            ),
        ),
        store=store,
        child_runner=_runner,
        max_child_runs=4,
        parent_abort_handle=parent_abort_handle,
    )

    assert called is False
    assert result.runs[0].status.value == "cancelled"
    assert result.runs[0].terminal_state.value == "cancelled"
    assert result.runs[0].metadata["terminal_reason"] == "operator_stop"
