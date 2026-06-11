"""Polling and collection helpers for parent/subagent state."""

from __future__ import annotations

from typing import Any

from agent_driver.subagents.mailbox import SubagentMailboxStore
from agent_driver.subagents.merge import summarize_child_runs_for_parent
from agent_driver.subagents.store import SubagentStore


def build_subagent_status_snapshot(
    *,
    store: SubagentStore,
    parent_run_id: str,
    mailbox_store: SubagentMailboxStore | None = None,
) -> dict[str, Any]:
    """Build a bounded status view for one parent run."""
    groups = store.list_groups(parent_run_id)
    runs = store.list_runs(parent_run_id)
    pending_mailbox = (
        mailbox_store.list_pending(parent_run_id=parent_run_id)
        if mailbox_store is not None
        else []
    )
    status_counts: dict[str, int] = {}
    for run in runs:
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "parent_run_id": parent_run_id,
        "group_count": len(groups),
        "run_count": len(runs),
        "status_counts": status_counts,
        "groups": [group.model_dump(mode="json") for group in groups],
        "runs": summarize_child_runs_for_parent(
            [run.model_dump(mode="json") for run in runs]
        ),
        "pending_mailbox_count": len(pending_mailbox),
        "pending_mailbox": [item.model_dump(mode="json") for item in pending_mailbox],
    }


def collect_subagent_mailbox(
    *,
    mailbox_store: SubagentMailboxStore,
    parent_run_id: str,
    mark_delivered: bool = True,
) -> list[dict[str, Any]]:
    """Collect pending mailbox items for a parent run."""
    pending = mailbox_store.list_pending(parent_run_id=parent_run_id)
    collected = []
    for item in pending:
        row = item
        if mark_delivered:
            row = mailbox_store.mark_delivered(item.mailbox_id) or item
        collected.append(row.model_dump(mode="json"))
    return collected


__all__ = ["build_subagent_status_snapshot", "collect_subagent_mailbox"]
