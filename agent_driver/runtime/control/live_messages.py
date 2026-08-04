"""Public helpers for versioned live-message capability and NEXT handoff."""

from __future__ import annotations

from collections.abc import Callable

from agent_driver.contracts.control import (
    CommandQueueItem,
    LiveMessageCapabilities,
    NextTurnHandoff,
)
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.control.protocols import CommandQueueStore


def live_message_capabilities(
    store: CommandQueueStore, *, durable_store: str
) -> LiveMessageCapabilities:
    """Return the capability only for a store implementing the complete seam."""
    required = (
        "admit",
        "list_for_run",
        "set_run_phase",
        "claim_for_boundary",
        "claim_hard_redirect",
        "commit_terminal",
        "claim_next_handoff",
        "complete_handoff",
        "cancel_next",
        "stop_run",
        "contract_schema_version",
        "quarantine_legacy_rows",
    )
    supported = all(callable(getattr(store, name, None)) for name in required)
    if supported:
        try:
            supported = int(store.contract_schema_version()) == 1
        except Exception:
            supported = False
    return LiveMessageCapabilities(
        soft_steer=supported,
        hard_redirect=supported,
        queue_next=supported,
        cancel_queued=supported,
        durable_store=durable_store,
    )


def live_message_receipt(item: CommandQueueItem) -> dict[str, object]:
    """Return the allowlisted raw-message-free projection for one transition."""
    return {
        "schema_version": item.schema_version,
        "queue_id": item.queue_id,
        "control_id": item.control_id,
        "run_id": item.run_id,
        "thread_id": item.thread_id,
        "kind": item.kind.value,
        "priority": item.priority.value,
        "sequence": item.sequence,
        "requested_semantic": (
            item.requested_semantic.value if item.requested_semantic else None
        ),
        "resolved_semantic": (
            item.resolved_semantic.value if item.resolved_semantic else None
        ),
        "state": item.status.value,
        "accepted_at": item.accepted_at or item.created_at,
        "applied_at": item.applied_at,
        "terminal_at": item.terminal_at,
        "accepted_phase": item.accepted_phase.value if item.accepted_phase else None,
        "applied_phase": item.applied_phase.value if item.applied_phase else None,
        "applies_at": item.applies_at,
        "reason_code": item.reason_code,
        "content_sha256": item.content_sha256,
        "source_generation": item.source_generation,
        "llm_generation": item.llm_generation,
        "superseded_generation": item.superseded_generation,
        "handoff_id": item.handoff_id,
        "destination_turn_id": item.destination_turn_id,
        "raw_free": True,
    }


def live_message_transition_event(item: CommandQueueItem) -> RuntimeEventType:
    """Resolve the typed event corresponding to the item's durable transition."""
    reason = item.reason_code
    if reason == "terminal_promoted_to_next":
        return RuntimeEventType.COMMAND_PROMOTED
    if reason == "run_stopped":
        return RuntimeEventType.COMMAND_STOP_PREEMPTED
    if reason == "next_handoff_completed":
        return RuntimeEventType.NEXT_HANDOFF_COMPLETED
    if reason == "redirect_claimed" or (
        item.resolved_semantic is not None
        and item.resolved_semantic.value == "redirect_current"
    ):
        return RuntimeEventType.COMMAND_REDIRECTED
    if item.status.value == "cancelled":
        return RuntimeEventType.COMMAND_CANCELLED
    if item.status.value == "failed":
        return RuntimeEventType.COMMAND_FAILED
    if item.status.value == "applied":
        return RuntimeEventType.COMMAND_APPLIED
    return RuntimeEventType.COMMAND_ACCEPTED


def dispatch_next_turn(
    *,
    store: CommandQueueStore,
    source_run_id: str,
    claimant_id: str,
    create_next_turn: Callable[[NextTurnHandoff], str],
    crash_after_host: bool = False,
    transition: Callable[[CommandQueueItem], None] | None = None,
) -> CommandQueueItem | None:
    """Perform one idempotent NEXT handoff through a host-owned turn seam.

    The queue keeps the same ``handoff_id`` across a dispatcher crash. The host
    must implement ``handoff_id -> destination_turn_id`` idempotently and append
    the user message with the same provenance before returning. Only then does
    this helper mark the generic source command applied.
    """
    item = store.claim_next_handoff(
        source_run_id=source_run_id, claimant_id=claimant_id
    )
    if item is None:
        return None
    message = item.payload.get("message") or item.payload.get("text")
    if not isinstance(message, str) or not item.handoff_id or not item.content_sha256:
        store.mark_failed(item.queue_id, error="invalid_next_handoff")
        failed = store.get(item.queue_id)
        if failed is not None and transition is not None:
            transition(failed)
        return failed
    handoff = NextTurnHandoff(
        handoff_id=item.handoff_id,
        queue_id=item.queue_id,
        source_run_id=item.run_id or source_run_id,
        source_thread_id=item.thread_id,
        message=message,
        content_sha256=item.content_sha256,
        sequence=item.sequence,
    )
    destination_turn_id = create_next_turn(handoff)
    if crash_after_host:
        return None
    completed = store.complete_handoff(
        item.queue_id,
        claimant_id=claimant_id,
        destination_turn_id=destination_turn_id,
    )
    if completed is not None and transition is not None:
        transition(completed)
    return completed


__all__ = [
    "dispatch_next_turn",
    "live_message_capabilities",
    "live_message_receipt",
    "live_message_transition_event",
]
