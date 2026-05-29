"""In-memory command queue store for steering control-plane tests/dev."""

from __future__ import annotations

from agent_driver.contracts.control import (
    CommandQueueItem,
    CommandQueueStatus,
    ControlPriority,
    ControlRequest,
    utc_now_iso,
)

_PRIORITY_ORDER = {
    ControlPriority.NOW: 0,
    ControlPriority.NEXT: 1,
    ControlPriority.LATER: 2,
}


class InMemoryCommandQueueStore:
    """Process-local FIFO command queue with priority ordering."""

    def __init__(self) -> None:
        self._items: dict[str, CommandQueueItem] = {}
        self._order: list[str] = []

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        """Persist a new queued command or return a deduped pending one."""
        existing = self._dedupe_match(request)
        if existing is not None:
            return existing
        item = CommandQueueItem.from_request(request)
        self._items[item.queue_id] = item
        self._order.append(item.queue_id)
        return item

    def get(self, queue_id: str) -> CommandQueueItem | None:
        """Return one command by id."""
        return self._items.get(queue_id)

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CommandQueueItem]:
        """Return queued commands ordered by priority and insertion order."""
        indexed = [
            (index, self._items[queue_id])
            for index, queue_id in enumerate(self._order)
            if queue_id in self._items
        ]
        pending = [
            (index, item)
            for index, item in indexed
            if item.status == CommandQueueStatus.QUEUED
            and _matches_route(
                item,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
        ]
        pending.sort(key=lambda row: (_PRIORITY_ORDER[row[1].priority], row[0]))
        return [item for _index, item in pending]

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
        item = self._items.get(queue_id)
        if item is None or item.status != CommandQueueStatus.QUEUED:
            return item
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.CANCELLED,
                "updated_at": utc_now_iso(),
                "cancelled_at": utc_now_iso(),
            }
        )
        self._items[queue_id] = updated
        return updated

    def mark_applied(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as applied."""
        item = self._items.get(queue_id)
        if item is None:
            return None
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.APPLIED,
                "updated_at": utc_now_iso(),
                "applied_at": utc_now_iso(),
            }
        )
        self._items[queue_id] = updated
        return updated

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
        """Mark a queued command as failed."""
        item = self._items.get(queue_id)
        if item is None:
            return None
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.FAILED,
                "updated_at": utc_now_iso(),
                "failed_at": utc_now_iso(),
                "error": error,
            }
        )
        self._items[queue_id] = updated
        return updated

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


__all__ = ["InMemoryCommandQueueStore"]
