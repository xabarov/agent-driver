"""Control helpers for parent-driven subagent lifecycle updates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent_driver.contracts.enums import SubagentStatus, SubagentTerminalState
from agent_driver.contracts.subagent_mailbox import (
    SubagentMailboxDirection,
    SubagentMailboxItem,
    SubagentMailboxKind,
)
from agent_driver.contracts.subagents import SubagentRun
from agent_driver.subagents.mailbox import SubagentMailboxStore
from agent_driver.subagents.store import SubagentStore


def find_subagent_run(
    store: SubagentStore,
    *,
    parent_run_id: str,
    subagent_run_id: str | None = None,
    child_run_id: str | None = None,
) -> SubagentRun | None:
    """Find one child row by subagent or child run id under a parent run."""
    subagent_run_id = _clean_optional_id(subagent_run_id)
    child_run_id = _clean_optional_id(child_run_id)
    if subagent_run_id is None and child_run_id is None:
        return None
    for row in store.list_runs(parent_run_id):
        if subagent_run_id is not None and row.subagent_run_id == subagent_run_id:
            return row
        if child_run_id is not None and row.child_run_id == child_run_id:
            return row
    return None


def append_subagent_continuation(
    store: SubagentStore,
    *,
    parent_run_id: str,
    message: str,
    subagent_run_id: str | None = None,
    child_run_id: str | None = None,
    source: str = "send_message_tool",
    metadata: dict[str, Any] | None = None,
    mailbox_store: SubagentMailboxStore | None = None,
) -> SubagentRun | None:
    """Append a parent-to-child continuation message to a child row."""
    text = str(message or "").strip()
    if not text:
        return None
    row = find_subagent_run(
        store,
        parent_run_id=parent_run_id,
        subagent_run_id=subagent_run_id,
        child_run_id=child_run_id,
    )
    if row is None:
        return None
    continuation = {
        "message": text,
        "source": source,
        "created_at": _utc_now(),
    }
    if metadata:
        continuation["metadata"] = metadata
    continuations = list(row.metadata.get("continuation_messages") or [])
    continuations.append(continuation)
    updated = row.model_copy(
        update={
            "metadata": {
                **row.metadata,
                "continuation_messages": continuations,
            }
        }
    )
    saved = store.upsert_run(updated)
    if mailbox_store is not None:
        mailbox_store.enqueue(
            SubagentMailboxItem(
                parent_run_id=parent_run_id,
                direction=SubagentMailboxDirection.PARENT_TO_CHILD,
                kind=SubagentMailboxKind.MESSAGE,
                subagent_run_id=saved.subagent_run_id,
                child_run_id=saved.child_run_id,
                payload={"message": text},
                source=source,
                dedupe_key=None,
                metadata=metadata or {},
            )
        )
    return saved


def stop_subagent_run(
    store: SubagentStore,
    *,
    parent_run_id: str,
    subagent_run_id: str | None = None,
    child_run_id: str | None = None,
    reason: str | None = None,
    source: str = "task_stop_tool",
) -> SubagentRun | None:
    """Mark one child run as cancelled by a parent-side control tool."""
    row = find_subagent_run(
        store,
        parent_run_id=parent_run_id,
        subagent_run_id=subagent_run_id,
        child_run_id=child_run_id,
    )
    if row is None:
        return None
    stopped_at = _utc_now()
    updated = row.model_copy(
        update={
            "status": SubagentStatus.CANCELLED,
            "terminal_state": SubagentTerminalState.CANCELLED,
            "metadata": {
                **row.metadata,
                "stop_requested": True,
                "stop_reason": str(reason or "parent_requested_stop"),
                "stop_source": source,
                "stopped_at": stopped_at,
            },
        }
    )
    return store.upsert_run(updated)


def _clean_optional_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "append_subagent_continuation",
    "find_subagent_run",
    "stop_subagent_run",
]
