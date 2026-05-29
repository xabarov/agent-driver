"""Tests for durable sqlite-backed subagent store."""

from __future__ import annotations

from agent_driver.contracts.enums import (
    SubagentExecutionMode,
    SubagentGroupStatus,
    SubagentJoinPolicy,
    SubagentStatus,
    SubagentTerminalState,
)
from agent_driver.contracts.subagents import MergeProvenance, SubagentGroup, SubagentRun
from agent_driver.subagents import SqliteSubagentStore


def test_sqlite_subagent_store_round_trip(tmp_path) -> None:
    """Sqlite store should persist group/run rows by parent run id."""
    store = SqliteSubagentStore(path=str(tmp_path / "subagents.sqlite3"))
    group = SubagentGroup(
        group_id="grp_sql_1",
        parent_run_id="run_sql_1",
        parent_attempt_id="att_sql_1",
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        status=SubagentGroupStatus.RUNNING,
    )
    run = SubagentRun(
        subagent_run_id="sub_sql_1",
        parent_run_id="run_sql_1",
        parent_attempt_id="att_sql_1",
        child_run_id="child_sql_1",
        task_id="task_sql_1",
        task_type="analysis",
        description="sqlite child",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.COMPLETED,
        terminal_state=SubagentTerminalState.SUCCEEDED,
        merge_provenance=MergeProvenance(strategy="test", source_kind="child"),
        metadata={"summary": "ok"},
    )
    store.upsert_group(group)
    store.upsert_run(run, idempotency_key="idemp-1")
    groups = store.list_groups("run_sql_1")
    runs = store.list_runs("run_sql_1")
    assert len(groups) == 1
    assert groups[0].group_id == "grp_sql_1"
    assert len(runs) == 1
    assert runs[0].subagent_run_id == "sub_sql_1"


def test_sqlite_subagent_store_idempotency_key_prevents_duplicate(tmp_path) -> None:
    """Same parent/idempotency key should return original persisted run."""
    store = SqliteSubagentStore(path=str(tmp_path / "subagents.sqlite3"))
    first = SubagentRun(
        subagent_run_id="sub_sql_1",
        parent_run_id="run_sql_1",
        parent_attempt_id="att_sql_1",
        task_id="task_sql_1",
        task_type="analysis",
        description="sqlite child",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.RUNNING,
        metadata={},
    )
    second = first.model_copy(update={"subagent_run_id": "sub_sql_2"})
    first_saved = store.upsert_run(first, idempotency_key="dup")
    second_saved = store.upsert_run(second, idempotency_key="dup")
    assert first_saved.subagent_run_id == second_saved.subagent_run_id
    assert len(store.list_runs("run_sql_1")) == 1


def test_sqlite_subagent_store_idempotency_key_updates_terminal_row(tmp_path) -> None:
    """Pending idempotent row should be replaced by terminal update."""
    store = SqliteSubagentStore(path=str(tmp_path / "subagents.sqlite3"))
    pending = SubagentRun(
        subagent_run_id="sub_sql_1",
        parent_run_id="run_sql_1",
        parent_attempt_id="att_sql_1",
        task_id="task_sql_1",
        task_type="analysis",
        description="sqlite child",
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

    rows = store.list_runs("run_sql_1")
    assert len(rows) == 1
    assert rows[0].subagent_run_id == "sub_sql_1"
    assert rows[0].status == SubagentStatus.COMPLETED
