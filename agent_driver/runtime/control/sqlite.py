"""SQLite command queue store for steering control-plane."""

from __future__ import annotations

from agent_driver.contracts.control import (
    CommandQueueItem,
    CommandQueueStatus,
    ControlPriority,
    ControlRequest,
    utc_now_iso,
)
from agent_driver.persistence import SqliteStoreBase

_PRIORITY_ORDER = {
    ControlPriority.NOW: 0,
    ControlPriority.NEXT: 1,
    ControlPriority.LATER: 2,
}


class SqliteCommandQueueStore(SqliteStoreBase):
    """SQLite-backed command queue store."""

    def __deepcopy__(self, memo: dict) -> "SqliteCommandQueueStore":
        """Return self — the store wraps a shared SQLite connection.

        ``create_agent`` deep-copies the ``RunnerConfig``; a live
        ``sqlite3.Connection`` is not copyable, and two independent copies of a
        shared command queue would defeat its purpose (the runner must read the
        same queue the host writes to). Identity-copy keeps the shared store.
        """
        memo[id(self)] = self
        return self

    def _init_schema(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS command_queue (
                queue_id TEXT PRIMARY KEY,
                control_id TEXT NOT NULL,
                run_id TEXT,
                thread_id TEXT,
                agent_id TEXT,
                priority TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                dedupe_key TEXT,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """)

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        """Persist a new queued command or return a deduped pending one."""
        existing = self._dedupe_match(request)
        if existing is not None:
            return existing
        item = CommandQueueItem.from_request(request)
        self._upsert(item)
        return item

    def get(self, queue_id: str) -> CommandQueueItem | None:
        """Return one command by id."""
        rows = self._query(
            "SELECT payload FROM command_queue WHERE queue_id = ?",
            (queue_id,),
        )
        if not rows:
            return None
        return CommandQueueItem.model_validate_json(rows[0][0])

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CommandQueueItem]:
        """Return queued commands ordered by priority and insertion order."""
        rows = self._query(
            """
            SELECT payload FROM command_queue
            WHERE status = ?
            ORDER BY created_at ASC, queue_id ASC
            """,
            (CommandQueueStatus.QUEUED.value,),
        )
        items = [
            item
            for (payload,) in rows
            if _matches_route(
                item := CommandQueueItem.model_validate_json(payload),
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
        ]
        items.sort(key=lambda item: (_PRIORITY_ORDER[item.priority], item.created_at))
        return items

    def dequeue_next(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> CommandQueueItem | None:
        """Return the next queued command without marking it applied."""
        pending = self.list_pending(
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
        )
        return pending[0] if pending else None

    def cancel(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as cancelled."""
        item = self.get(queue_id)
        if item is None or item.status != CommandQueueStatus.QUEUED:
            return item
        now = utc_now_iso()
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.CANCELLED,
                "updated_at": now,
                "cancelled_at": now,
            }
        )
        self._upsert(updated)
        return updated

    def mark_applied(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as applied."""
        item = self.get(queue_id)
        if item is None:
            return None
        now = utc_now_iso()
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.APPLIED,
                "updated_at": now,
                "applied_at": now,
            }
        )
        self._upsert(updated)
        return updated

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
        """Mark a queued command as failed."""
        item = self.get(queue_id)
        if item is None:
            return None
        now = utc_now_iso()
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.FAILED,
                "updated_at": now,
                "failed_at": now,
                "error": error,
            }
        )
        self._upsert(updated)
        return updated

    def _upsert(self, item: CommandQueueItem) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO command_queue (
                queue_id, control_id, run_id, thread_id, agent_id, priority, kind,
                status, source, dedupe_key, created_at, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.queue_id,
                item.control_id,
                item.run_id,
                item.thread_id,
                item.agent_id,
                item.priority.value,
                item.kind.value,
                item.status.value,
                item.source,
                item.dedupe_key,
                item.created_at,
                item.model_dump_json(),
            ),
        )

    def _dedupe_match(self, request: ControlRequest) -> CommandQueueItem | None:
        if not request.dedupe_key:
            return None
        for item in self.list_pending(
            run_id=request.run_id,
            thread_id=request.thread_id,
            agent_id=request.agent_id,
        ):
            if (
                item.kind == request.kind
                and item.source == request.source
                and item.dedupe_key == request.dedupe_key
            ):
                return item
        return None


def _matches_route(
    item: CommandQueueItem,
    *,
    run_id: str | None,
    thread_id: str | None,
    agent_id: str | None,
) -> bool:
    if run_id is not None and item.run_id != run_id:
        return False
    if thread_id is not None and item.thread_id != thread_id:
        return False
    if agent_id is not None and item.agent_id != agent_id:
        return False
    return True


__all__ = ["SqliteCommandQueueStore"]
