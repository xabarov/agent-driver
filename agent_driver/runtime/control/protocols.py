"""Command queue store protocols for steering control-plane."""

from __future__ import annotations

from typing import Protocol

from agent_driver.contracts.control import (
    CommandQueueItem,
    ControlRequest,
    LiveMessagePhase,
    LiveRunState,
)


class CommandQueueStore(Protocol):
    """Storage contract for queued steering commands."""

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        """Persist a new queued command or return a deduped pending one."""
        ...

    def get(self, queue_id: str) -> CommandQueueItem | None:
        """Return one command by id."""
        ...

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CommandQueueItem]:
        """Return queued commands ordered by priority and insertion order."""
        ...

    def list_for_run(self, run_id: str) -> list[CommandQueueItem]:
        """Return every durable command state for one run in FIFO order."""
        ...

    def dequeue_next(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> CommandQueueItem | None:
        """Return the next queued command without marking it applied."""
        ...

    def cancel(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as cancelled."""
        ...

    def mark_applied(
        self,
        queue_id: str,
        *,
        claimant_id: str | None = None,
        applied_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem | None:
        """Mark a queued command as applied."""
        ...

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
        """Mark a queued command as failed."""
        ...

    def admit(
        self,
        request: ControlRequest,
        *,
        accepted_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem:
        """Atomically admit a live message while its source run is nonterminal."""
        ...

    def set_run_phase(
        self,
        run_id: str,
        phase: LiveMessagePhase,
        *,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> LiveRunState:
        """Persist the current generic phase without reversing terminal state."""
        ...

    def get_run_state(self, run_id: str) -> LiveRunState | None:
        """Read the current phase and LLM generation fence."""
        ...

    def current_llm_generation(self, run_id: str) -> int:
        """Return the durable current LLM generation."""
        ...

    def claim_for_boundary(
        self,
        *,
        run_id: str,
        claimant_id: str,
        applied_phase: LiveMessagePhase,
    ) -> CommandQueueItem | None:
        """Claim one current-turn command; NEXT is never eligible here."""
        ...

    def claim_hard_redirect(
        self, *, run_id: str, claimant_id: str, expected_generation: int
    ) -> CommandQueueItem | None:
        """Claim one redirect and atomically advance the LLM generation."""
        ...

    def release_claim(
        self, queue_id: str, *, claimant_id: str
    ) -> CommandQueueItem | None:
        """Release a crash/retry claim without changing command truth."""
        ...

    def commit_terminal(
        self, run_id: str, *, stopped: bool = False
    ) -> list[CommandQueueItem]:
        """Commit terminal state and promote or preempt accepted commands."""
        ...

    def stop_run(self, run_id: str) -> list[CommandQueueItem]:
        """Commit Stop and fail every still-pending live message."""
        ...

    def cancel_next(self, queue_id: str) -> CommandQueueItem | None:
        """Cancel a still-pending NEXT message only."""
        ...

    def claim_next_handoff(
        self, *, source_run_id: str, claimant_id: str
    ) -> CommandQueueItem | None:
        """Claim the oldest NEXT item after source terminal."""
        ...

    def complete_handoff(
        self,
        queue_id: str,
        *,
        claimant_id: str,
        destination_turn_id: str,
    ) -> CommandQueueItem | None:
        """Mark NEXT applied after the host durably created turn and message."""
        ...

    def contract_schema_version(self) -> int:
        """Return the persisted live-message contract schema version."""
        ...

    def quarantine_legacy_rows(self) -> list[CommandQueueItem]:
        """Fail ambiguous rows created before semantic versioning."""
        ...


__all__ = ["CommandQueueStore"]
