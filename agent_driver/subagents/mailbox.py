"""Durable mailbox stores for parent/subagent coordination."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_driver.contracts.control import utc_now_iso
from agent_driver.contracts.subagent_mailbox import (
    SubagentMailboxItem,
    SubagentMailboxStatus,
)


class SubagentMailboxStore(Protocol):
    """Storage contract for subagent mailbox items."""

    def enqueue(self, item: SubagentMailboxItem) -> SubagentMailboxItem: ...
    def get(self, mailbox_id: str) -> SubagentMailboxItem | None: ...
    def list_pending(
        self,
        *,
        parent_run_id: str | None = None,
        subagent_run_id: str | None = None,
        child_run_id: str | None = None,
    ) -> list[SubagentMailboxItem]: ...
    def list_for_parent(self, parent_run_id: str) -> list[SubagentMailboxItem]: ...
    def mark_delivered(self, mailbox_id: str) -> SubagentMailboxItem | None: ...
    def acknowledge(self, mailbox_id: str) -> SubagentMailboxItem | None: ...
    def cancel(self, mailbox_id: str) -> SubagentMailboxItem | None: ...


@dataclass(slots=True)
class InMemorySubagentMailboxStore:
    """Process-local subagent mailbox store for tests/dev."""

    _items: dict[str, SubagentMailboxItem] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def enqueue(self, item: SubagentMailboxItem) -> SubagentMailboxItem:
        """Persist a mailbox item or return a deduped queued item."""
        existing = self._dedupe_match(item)
        if existing is not None:
            return existing
        self._items[item.mailbox_id] = item
        self._order.append(item.mailbox_id)
        return item

    def get(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Return one mailbox item by id."""
        return self._items.get(mailbox_id)

    def list_pending(
        self,
        *,
        parent_run_id: str | None = None,
        subagent_run_id: str | None = None,
        child_run_id: str | None = None,
    ) -> list[SubagentMailboxItem]:
        """Return queued mailbox items in insertion order."""
        return [
            item
            for item in self._ordered_items()
            if item.status == SubagentMailboxStatus.QUEUED
            and _matches_route(
                item,
                parent_run_id=parent_run_id,
                subagent_run_id=subagent_run_id,
                child_run_id=child_run_id,
            )
        ]

    def list_for_parent(self, parent_run_id: str) -> list[SubagentMailboxItem]:
        """Return all mailbox items for one parent run."""
        return [
            item
            for item in self._ordered_items()
            if item.parent_run_id == parent_run_id
        ]

    def mark_delivered(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Mark a queued mailbox item as delivered."""
        return self._update_status(
            mailbox_id,
            status=SubagentMailboxStatus.DELIVERED,
            timestamp_field="delivered_at",
        )

    def acknowledge(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Mark a mailbox item as acknowledged."""
        return self._update_status(
            mailbox_id,
            status=SubagentMailboxStatus.ACKNOWLEDGED,
            timestamp_field="acknowledged_at",
        )

    def cancel(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Mark a mailbox item as cancelled."""
        return self._update_status(
            mailbox_id,
            status=SubagentMailboxStatus.CANCELLED,
            timestamp_field="cancelled_at",
        )

    def _ordered_items(self) -> list[SubagentMailboxItem]:
        return [
            self._items[item_id] for item_id in self._order if item_id in self._items
        ]

    def _update_status(
        self,
        mailbox_id: str,
        *,
        status: SubagentMailboxStatus,
        timestamp_field: str,
    ) -> SubagentMailboxItem | None:
        item = self._items.get(mailbox_id)
        if item is None:
            return None
        now = utc_now_iso()
        updated = item.model_copy(
            update={"status": status, "updated_at": now, timestamp_field: now}
        )
        self._items[mailbox_id] = updated
        return updated

    def _dedupe_match(self, item: SubagentMailboxItem) -> SubagentMailboxItem | None:
        if not item.dedupe_key:
            return None
        for existing in self.list_pending(parent_run_id=item.parent_run_id):
            if (
                existing.direction == item.direction
                and existing.kind == item.kind
                and existing.source == item.source
                and existing.dedupe_key == item.dedupe_key
            ):
                return existing
        return None


class SqliteSubagentMailboxStore:
    """SQLite-backed durable subagent mailbox store."""

    def __init__(self, *, path: str) -> None:
        self._path = Path(path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subagent_mailbox (
                    mailbox_id TEXT PRIMARY KEY,
                    parent_run_id TEXT NOT NULL,
                    subagent_run_id TEXT,
                    child_run_id TEXT,
                    dedupe_key TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS subagent_mailbox_dedupe_idx
                ON subagent_mailbox(parent_run_id, dedupe_key)
                WHERE dedupe_key IS NOT NULL AND status = 'queued'
                """)

    def enqueue(self, item: SubagentMailboxItem) -> SubagentMailboxItem:
        """Persist a mailbox item or return a deduped queued item."""
        existing = self._dedupe_match(item)
        if existing is not None:
            return existing
        self._upsert(item)
        return item

    def get(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Return one mailbox item by id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM subagent_mailbox WHERE mailbox_id = ?",
                (mailbox_id,),
            ).fetchone()
        return SubagentMailboxItem.model_validate_json(row["payload"]) if row else None

    def list_pending(
        self,
        *,
        parent_run_id: str | None = None,
        subagent_run_id: str | None = None,
        child_run_id: str | None = None,
    ) -> list[SubagentMailboxItem]:
        """Return queued mailbox items in insertion order."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM subagent_mailbox
                WHERE status = ?
                ORDER BY created_at ASC, mailbox_id ASC
                """,
                (SubagentMailboxStatus.QUEUED.value,),
            ).fetchall()
        return [
            item
            for row in rows
            if _matches_route(
                item := SubagentMailboxItem.model_validate_json(row["payload"]),
                parent_run_id=parent_run_id,
                subagent_run_id=subagent_run_id,
                child_run_id=child_run_id,
            )
        ]

    def list_for_parent(self, parent_run_id: str) -> list[SubagentMailboxItem]:
        """Return all mailbox items for one parent run."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM subagent_mailbox
                WHERE parent_run_id = ?
                ORDER BY created_at ASC, mailbox_id ASC
                """,
                (parent_run_id,),
            ).fetchall()
        return [SubagentMailboxItem.model_validate_json(row["payload"]) for row in rows]

    def mark_delivered(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Mark a queued mailbox item as delivered."""
        return self._update_status(
            mailbox_id,
            status=SubagentMailboxStatus.DELIVERED,
            timestamp_field="delivered_at",
        )

    def acknowledge(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Mark a mailbox item as acknowledged."""
        return self._update_status(
            mailbox_id,
            status=SubagentMailboxStatus.ACKNOWLEDGED,
            timestamp_field="acknowledged_at",
        )

    def cancel(self, mailbox_id: str) -> SubagentMailboxItem | None:
        """Mark a mailbox item as cancelled."""
        return self._update_status(
            mailbox_id,
            status=SubagentMailboxStatus.CANCELLED,
            timestamp_field="cancelled_at",
        )

    def _upsert(self, item: SubagentMailboxItem) -> None:
        payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_mailbox (
                    mailbox_id, parent_run_id, subagent_run_id, child_run_id,
                    dedupe_key, status, created_at, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mailbox_id) DO UPDATE SET
                    parent_run_id=excluded.parent_run_id,
                    subagent_run_id=excluded.subagent_run_id,
                    child_run_id=excluded.child_run_id,
                    dedupe_key=excluded.dedupe_key,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    payload=excluded.payload
                """,
                (
                    item.mailbox_id,
                    item.parent_run_id,
                    item.subagent_run_id,
                    item.child_run_id,
                    item.dedupe_key,
                    item.status.value,
                    item.created_at,
                    payload,
                ),
            )

    def _update_status(
        self,
        mailbox_id: str,
        *,
        status: SubagentMailboxStatus,
        timestamp_field: str,
    ) -> SubagentMailboxItem | None:
        item = self.get(mailbox_id)
        if item is None:
            return None
        now = utc_now_iso()
        updated = item.model_copy(
            update={"status": status, "updated_at": now, timestamp_field: now}
        )
        self._upsert(updated)
        return updated

    def _dedupe_match(self, item: SubagentMailboxItem) -> SubagentMailboxItem | None:
        if not item.dedupe_key:
            return None
        for existing in self.list_pending(parent_run_id=item.parent_run_id):
            if (
                existing.direction == item.direction
                and existing.kind == item.kind
                and existing.source == item.source
                and existing.dedupe_key == item.dedupe_key
            ):
                return existing
        return None


def _matches_route(
    item: SubagentMailboxItem,
    *,
    parent_run_id: str | None,
    subagent_run_id: str | None,
    child_run_id: str | None,
) -> bool:
    if parent_run_id is not None and item.parent_run_id != parent_run_id:
        return False
    if subagent_run_id is not None and item.subagent_run_id != subagent_run_id:
        return False
    if child_run_id is not None and item.child_run_id != child_run_id:
        return False
    return True


__all__ = [
    "InMemorySubagentMailboxStore",
    "SqliteSubagentMailboxStore",
    "SubagentMailboxStore",
]
