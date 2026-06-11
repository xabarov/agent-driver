"""Subagent store tests."""

from __future__ import annotations

from agent_driver.contracts.enums import (
    SubagentExecutionMode,
    SubagentGroupStatus,
    SubagentJoinPolicy,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.subagents import MergeProvenance, SubagentGroup, SubagentRun
from agent_driver.subagents import InMemorySubagentStore


def test_subagent_store_idempotent_spawn() -> None:
    """Repeated idempotency key should return same run row."""
    store = InMemorySubagentStore()
    first = store.upsert_run(
        SubagentRun(
            subagent_run_id="sub_1",
            parent_run_id="run_1",
            parent_attempt_id="att_1",
            task_id="task_1",
            task_type="analysis",
            description="child",
            execution_mode=SubagentExecutionMode.SYNC,
            fanout_slot=1,
            status=SubagentStatus.RUNNING,
            metadata={},
        ),
        idempotency_key="dup",
    )
    second = store.upsert_run(
        SubagentRun(
            subagent_run_id="sub_2",
            parent_run_id="run_1",
            parent_attempt_id="att_1",
            task_id="task_1",
            task_type="analysis",
            description="child",
            execution_mode=SubagentExecutionMode.SYNC,
            fanout_slot=1,
            status=SubagentStatus.RUNNING,
            metadata={},
        ),
        idempotency_key="dup",
    )
    assert first.subagent_run_id == second.subagent_run_id
    assert len(store.list_runs("run_1")) == 1


def test_subagent_store_idempotent_spawn_updates_terminal_row() -> None:
    """Pending idempotent row should be replaced by terminal update."""
    store = InMemorySubagentStore()
    pending = SubagentRun(
        subagent_run_id="sub_1",
        parent_run_id="run_1",
        parent_attempt_id="att_1",
        task_id="task_1",
        task_type="analysis",
        description="child",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.RUNNING,
        metadata={},
    )
    completed = pending.model_copy(
        update={
            "status": SubagentStatus.COMPLETED,
            "terminal_state": SubagentTerminalState.SUCCEEDED,
            "merge_provenance": MergeProvenance(strategy="test", source_kind="child"),
        }
    )

    store.upsert_run(pending, idempotency_key="dup")
    store.upsert_run(completed, idempotency_key="dup")

    rows = store.list_runs("run_1")
    assert len(rows) == 1
    assert rows[0].subagent_run_id == "sub_1"
    assert rows[0].status == SubagentStatus.COMPLETED


def test_subagent_group_persist_and_replace() -> None:
    """Group upsert should replace by group_id."""
    store = InMemorySubagentStore()
    group = SubagentGroup(
        group_id="grp_1",
        parent_run_id="run_1",
        parent_attempt_id="att_1",
        join_policy=SubagentJoinPolicy.WAIT_ALL,
    )
    store.upsert_group(group)
    updated = group.model_copy(update={"status": SubagentGroupStatus.RUNNING})
    store.upsert_group(updated)
    rows = store.list_groups("run_1")
    assert len(rows) == 1
    assert rows[0].status.value == "running"
