"""P3a H11 — observability callback for subagent group transitions.

Tests the new ``on_event`` parameter on ``execute_subagent_group_sync``:

- emits subagent_group_started exactly once at entry
- emits subagent_started + subagent_completed for each child (in order)
- emits subagent_group_joined when the join policy is satisfied
- emits subagent_group_failed when the join is not done (e.g. ALL with
  failing child)
- callback exceptions are swallowed (executor must not break on host bug)
- omitting on_event preserves prior behaviour (no events, no exception)
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.contracts.enums import SubagentJoinPolicy as JoinPolicy
from agent_driver.contracts.enums import SubagentMergeMode as MergeMode
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
                context={
                    "run_id": run_input.run_id or "child",
                    "attempt_id": "att_child",
                    "seq": 1,
                },
            )
        ],
        answer="child answer",
    )


async def _fail_child_runner(run_input):
    return AgentRunOutput(
        run_id=run_input.run_id or "child",
        attempt_id="att_child",
        status=RunStatus.FAILED,
        terminal_reason=TerminalReason.RUNTIME_ERROR,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_FAILED,
                context={
                    "run_id": run_input.run_id or "child",
                    "attempt_id": "att_child",
                    "seq": 1,
                },
            )
        ],
        answer="",
    )


def _group_spec(
    *task_ids: str, join_policy: JoinPolicy = JoinPolicy.WAIT_ALL
) -> SubagentGroupSpec:
    return SubagentGroupSpec(
        group_id="grp_obs",
        purpose="analysis",
        join_policy=join_policy,
        merge_mode=MergeMode.APPEND,
        tasks=tuple(
            SubagentTaskSpec(task_id=tid, task=f"task-{tid}", description="desc")
            for tid in task_ids
        ),
    )


@pytest.mark.asyncio
async def test_emits_group_started_once_at_entry() -> None:
    events: list[tuple[str, dict]] = []
    store = InMemorySubagentStore()
    await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent"),
        group_spec=_group_spec("t1", "t2"),
        store=store,
        child_runner=_ok_child_runner,
        max_child_runs=4,
        on_event=lambda etype, payload: events.append((etype, payload)),
    )
    starts = [e for e in events if e[0] == "subagent_group_started"]
    assert len(starts) == 1
    p = starts[0][1]
    assert p["group_id"] == "grp_obs"
    assert p["task_count"] == 2


@pytest.mark.asyncio
async def test_emits_per_child_started_and_completed_in_order() -> None:
    events: list[tuple[str, dict]] = []
    await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent"),
        group_spec=_group_spec("a", "b", "c"),
        store=InMemorySubagentStore(),
        child_runner=_ok_child_runner,
        max_child_runs=4,
        on_event=lambda etype, payload: events.append((etype, payload)),
    )
    # Sequence: group_started, (started a, completed a, started b, completed b, started c, completed c), group_joined
    types = [e[0] for e in events]
    assert types == [
        "subagent_group_started",
        "subagent_started",
        "subagent_completed",
        "subagent_started",
        "subagent_completed",
        "subagent_started",
        "subagent_completed",
        "subagent_group_joined",
    ]
    # task_id sequence preserved
    started_ids = [e[1]["task_id"] for e in events if e[0] == "subagent_started"]
    completed_ids = [e[1]["task_id"] for e in events if e[0] == "subagent_completed"]
    assert started_ids == ["a", "b", "c"]
    assert completed_ids == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_emits_group_joined_when_join_policy_satisfied() -> None:
    events: list[tuple[str, dict]] = []
    await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent"),
        group_spec=_group_spec("ok1", "ok2", join_policy=JoinPolicy.WAIT_ALL),
        store=InMemorySubagentStore(),
        child_runner=_ok_child_runner,
        max_child_runs=4,
        on_event=lambda etype, payload: events.append((etype, payload)),
    )
    terminals = [
        e for e in events if e[0] in ("subagent_group_joined", "subagent_group_failed")
    ]
    assert terminals[0][0] == "subagent_group_joined"
    payload = terminals[0][1]
    assert payload["completed_count"] == 2
    assert payload["failed_count"] == 0


@pytest.mark.asyncio
async def test_emits_group_failed_when_join_not_done() -> None:
    """WAIT_ANY needs ≥1 completed; all-failing children → done=False → group_failed event."""
    events: list[tuple[str, dict]] = []

    await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent"),
        group_spec=SubagentGroupSpec(
            group_id="grp_all_fail",
            purpose="analysis",
            join_policy=JoinPolicy.WAIT_ANY,
            tasks=(
                SubagentTaskSpec(task_id="fail1", task="t1", description="d"),
                SubagentTaskSpec(task_id="fail2", task="t2", description="d"),
            ),
        ),
        store=InMemorySubagentStore(),
        child_runner=_fail_child_runner,
        max_child_runs=4,
        on_event=lambda etype, payload: events.append((etype, payload)),
    )
    terminals = [
        e for e in events if e[0] in ("subagent_group_joined", "subagent_group_failed")
    ]
    assert terminals[0][0] == "subagent_group_failed"
    assert terminals[0][1]["failed_count"] == 2


@pytest.mark.asyncio
async def test_callback_exceptions_are_swallowed() -> None:
    """A buggy on_event must not break execution."""

    def boom(_etype, _payload):
        raise RuntimeError("buggy callback")

    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent"),
        group_spec=_group_spec("t1"),
        store=InMemorySubagentStore(),
        child_runner=_ok_child_runner,
        max_child_runs=4,
        on_event=boom,
    )
    # Result envelope intact despite the callback throwing on every event.
    assert result.join_state == "joined"
    assert len(result.runs) == 1


@pytest.mark.asyncio
async def test_omitting_on_event_preserves_prior_behaviour() -> None:
    """No callback → no exception, same SubagentExecutionResult shape."""
    result = await execute_subagent_group_sync(
        parent=default_parent_handoff(answer="parent"),
        group_spec=_group_spec("t1", "t2"),
        store=InMemorySubagentStore(),
        child_runner=_ok_child_runner,
        max_child_runs=4,
    )
    assert result.join_state == "joined"
    assert len(result.runs) == 2
