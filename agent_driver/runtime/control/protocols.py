"""Command queue store protocols for steering control-plane."""

from __future__ import annotations

from typing import Protocol

from agent_driver.contracts.control import CommandQueueItem, ControlRequest


class CommandQueueStore(Protocol):
    """Storage contract for queued steering commands."""

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        """Persist a new queued command or return a deduped pending one."""

    def get(self, queue_id: str) -> CommandQueueItem | None:
        """Return one command by id."""

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CommandQueueItem]:
        """Return queued commands ordered by priority and insertion order."""

    def dequeue_next(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> CommandQueueItem | None:
        """Return the next queued command without marking it applied."""

    def cancel(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as cancelled."""

    def mark_applied(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as applied."""

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
        """Mark a queued command as failed."""


__all__ = ["CommandQueueStore"]
