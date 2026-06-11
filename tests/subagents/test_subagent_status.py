"""Tests for subagent polling and mailbox collection helpers."""

from __future__ import annotations

from agent_driver.contracts import (
    SubagentExecutionMode,
    SubagentGroup,
    SubagentJoinPolicy,
    SubagentMailboxDirection,
    SubagentMailboxItem,
    SubagentMailboxKind,
    SubagentRun,
    SubagentStatus,
)
from agent_driver.subagents import (
    InMemorySubagentMailboxStore,
    InMemorySubagentStore,
    build_subagent_status_snapshot,
    collect_subagent_mailbox,
)


def test_build_subagent_status_snapshot_includes_runs_groups_and_mailbox() -> None:
    """Status snapshot should expose bounded parent/subagent state."""
    store = InMemorySubagentStore()
    mailbox_store = InMemorySubagentMailboxStore()
    store.upsert_group(
        SubagentGroup(
            group_id="grp_1",
            parent_run_id="parent_1",
            parent_attempt_id="att_1",
            join_policy=SubagentJoinPolicy.WAIT_ALL,
        )
    )
    store.upsert_run(_run_row())
    mailbox_store.enqueue(_mailbox_item())

    snapshot = build_subagent_status_snapshot(
        store=store,
        mailbox_store=mailbox_store,
        parent_run_id="parent_1",
    )

    assert snapshot["group_count"] == 1
    assert snapshot["run_count"] == 1
    assert snapshot["status_counts"] == {"running": 1}
    assert snapshot["pending_mailbox_count"] == 1
    assert snapshot["pending_mailbox"][0]["kind"] == "task_notification"
    assert snapshot["runs"][0]["subagent_run_id"] == "sub_1"


def test_collect_subagent_mailbox_marks_items_delivered() -> None:
    """Collection should optionally advance mailbox lifecycle."""
    mailbox_store = InMemorySubagentMailboxStore()
    item = mailbox_store.enqueue(_mailbox_item())

    collected = collect_subagent_mailbox(
        mailbox_store=mailbox_store,
        parent_run_id="parent_1",
    )

    assert collected[0]["mailbox_id"] == item.mailbox_id
    assert collected[0]["status"] == "delivered"
    assert mailbox_store.list_pending(parent_run_id="parent_1") == []


def _run_row() -> SubagentRun:
    return SubagentRun(
        subagent_run_id="sub_1",
        parent_run_id="parent_1",
        parent_attempt_id="att_1",
        child_run_id="child_1",
        task_id="task_1",
        task_type="analysis",
        description="child",
        execution_mode=SubagentExecutionMode.SYNC,
        fanout_slot=1,
        status=SubagentStatus.RUNNING,
    )


def _mailbox_item() -> SubagentMailboxItem:
    return SubagentMailboxItem(
        parent_run_id="parent_1",
        direction=SubagentMailboxDirection.CHILD_TO_PARENT,
        kind=SubagentMailboxKind.TASK_NOTIFICATION,
        subagent_run_id="sub_1",
        child_run_id="child_1",
        payload={"message": "done"},
    )
