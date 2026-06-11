"""Tests for durable subagent mailbox stores."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    SubagentMailboxDirection,
    SubagentMailboxItem,
    SubagentMailboxKind,
    SubagentMailboxStatus,
)
from agent_driver.subagents import (
    InMemorySubagentMailboxStore,
    SqliteSubagentMailboxStore,
)


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_subagent_mailbox_lifecycle(store_kind: str, tmp_path) -> None:
    """Mailbox stores should preserve queued/delivered/ack states."""
    store = (
        SqliteSubagentMailboxStore(path=str(tmp_path / "mailbox.sqlite3"))
        if store_kind == "sqlite"
        else InMemorySubagentMailboxStore()
    )
    item = store.enqueue(_message_item())

    assert store.get(item.mailbox_id) == item
    assert store.list_pending(parent_run_id="parent_1") == [item]
    delivered = store.mark_delivered(item.mailbox_id)
    assert delivered is not None
    assert delivered.status == SubagentMailboxStatus.DELIVERED
    assert delivered.delivered_at is not None
    assert store.list_pending(parent_run_id="parent_1") == []
    acknowledged = store.acknowledge(item.mailbox_id)
    assert acknowledged is not None
    assert acknowledged.status == SubagentMailboxStatus.ACKNOWLEDGED
    assert acknowledged.acknowledged_at is not None


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_subagent_mailbox_dedupes_pending_items(store_kind: str, tmp_path) -> None:
    """Mailbox stores should dedupe only pending items by parent/dedupe key."""
    store = (
        SqliteSubagentMailboxStore(path=str(tmp_path / "mailbox.sqlite3"))
        if store_kind == "sqlite"
        else InMemorySubagentMailboxStore()
    )
    first = store.enqueue(_message_item(dedupe_key="same"))
    second = store.enqueue(_message_item(dedupe_key="same"))
    store.mark_delivered(first.mailbox_id)
    third = store.enqueue(_message_item(dedupe_key="same"))

    assert first.mailbox_id == second.mailbox_id
    assert third.mailbox_id != first.mailbox_id
    assert len(store.list_for_parent("parent_1")) == 2


def test_sqlite_subagent_mailbox_survives_reopen(tmp_path) -> None:
    """SQLite mailbox should persist rows across store instances."""
    path = tmp_path / "mailbox.sqlite3"
    first_store = SqliteSubagentMailboxStore(path=str(path))
    item = first_store.enqueue(_message_item())

    second_store = SqliteSubagentMailboxStore(path=str(path))

    assert second_store.get(item.mailbox_id) == item
    assert second_store.list_pending(subagent_run_id="sub_1")[0].payload == {
        "message": "continue"
    }


def _message_item(*, dedupe_key: str | None = None) -> SubagentMailboxItem:
    return SubagentMailboxItem(
        parent_run_id="parent_1",
        direction=SubagentMailboxDirection.PARENT_TO_CHILD,
        kind=SubagentMailboxKind.MESSAGE,
        subagent_run_id="sub_1",
        child_run_id="child_1",
        payload={"message": "continue"},
        source="test",
        dedupe_key=dedupe_key,
    )
