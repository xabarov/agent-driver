"""In-memory command queue store for steering control-plane tests/dev."""

from __future__ import annotations

from threading import RLock

from agent_driver.contracts.control import (
    CommandQueueItem,
    CommandQueueStatus,
    ControlKind,
    ControlPriority,
    ControlRequest,
    LiveMessageAdmissionError,
    LiveMessageIdempotencyError,
    LiveMessagePhase,
    LiveMessageSemantic,
    LiveRunState,
    control_request_sha256,
    requested_semantic_for_request,
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
        self._run_states: dict[str, LiveRunState] = {}
        self._lock = RLock()

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        """Persist a new queued command or return a deduped pending one."""
        if (
            request.run_id in self._run_states
            and requested_semantic_for_request(request) is not None
        ):
            return self.admit(request)
        with self._lock:
            existing = self._dedupe_match(request)
            if existing is not None:
                return existing
            return self._insert(request, accepted_phase=LiveMessagePhase.UNKNOWN)

    def admit(
        self,
        request: ControlRequest,
        *,
        accepted_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem:
        """Atomically admit a live message while its source run is nonterminal."""
        if request.run_id is None:
            raise LiveMessageAdmissionError("run_id_required")
        with self._lock:
            existing = self._dedupe_match(request)
            if existing is not None:
                return existing
            state = self._run_states.get(request.run_id)
            phase = accepted_phase or (state.phase if state is not None else None)
            if state is None or phase in (None, LiveMessagePhase.UNKNOWN):
                raise LiveMessageAdmissionError("live_message_state_unavailable")
            if state.phase is LiveMessagePhase.TERMINAL:
                raise LiveMessageAdmissionError("turn_no_longer_steerable")
            if state.stopped:
                raise LiveMessageAdmissionError("run_stopped")
            semantic = requested_semantic_for_request(request)
            if semantic is None:
                raise LiveMessageAdmissionError("unsupported_live_message_semantic")
            if semantic in (
                LiveMessageSemantic.STEER_CURRENT,
                LiveMessageSemantic.REDIRECT_CURRENT,
                LiveMessageSemantic.QUEUE_NEXT,
            ):
                message = request.payload.get("message") or request.payload.get("text")
                if not isinstance(message, str) or not message.strip():
                    raise LiveMessageAdmissionError("invalid_control_payload")
            item = self._insert(request, accepted_phase=phase).model_copy(
                update={"source_generation": state.llm_generation}
            )
            self._items[item.queue_id] = item
            if semantic is LiveMessageSemantic.STOP:
                now = utc_now_iso()
                self._run_states[request.run_id] = state.model_copy(
                    update={"stopped": True, "updated_at": now}
                )
                item = item.model_copy(
                    update={"reason_code": "stop_accepted", "updated_at": now}
                )
                self._items[item.queue_id] = item
                for pending in self.list_pending(run_id=request.run_id):
                    if pending.queue_id == item.queue_id:
                        continue
                    if pending.requested_semantic in (
                        LiveMessageSemantic.STEER_CURRENT,
                        LiveMessageSemantic.REDIRECT_CURRENT,
                        LiveMessageSemantic.QUEUE_NEXT,
                    ):
                        self.mark_failed(pending.queue_id, error="run_stopped")
            return item

    def _insert(
        self, request: ControlRequest, *, accepted_phase: LiveMessagePhase
    ) -> CommandQueueItem:
        item = CommandQueueItem.from_request(request).model_copy(
            update={
                "sequence": len(self._order) + 1,
                "accepted_phase": accepted_phase,
            }
        )
        if item.requested_semantic is LiveMessageSemantic.QUEUE_NEXT:
            item = item.model_copy(update={"handoff_id": _handoff_id(item.queue_id)})
        self._items[item.queue_id] = item
        self._order.append(item.queue_id)
        return item

    def get(self, queue_id: str) -> CommandQueueItem | None:
        """Return one command by id."""
        with self._lock:
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
        pending.sort(key=lambda row: (_dispatch_order(row[1]), row[0]))
        return [item for _index, item in pending]

    def list_for_run(self, run_id: str) -> list[CommandQueueItem]:
        """Return every command state for one run in FIFO order."""
        with self._lock:
            return [
                self._items[queue_id]
                for queue_id in self._order
                if self._items[queue_id].run_id == run_id
            ]

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
        if item is not None and item.requested_semantic is LiveMessageSemantic.QUEUE_NEXT:
            return self.cancel_next(queue_id)
        with self._lock:
            return self._cancel_unlocked(queue_id, reason_code="cancelled")

    def _cancel_unlocked(
        self, queue_id: str, *, reason_code: str
    ) -> CommandQueueItem | None:
        item = self._items.get(queue_id)
        if item is None or item.status != CommandQueueStatus.QUEUED:
            return item
        if item.claimed_by is not None:
            return item
        now = utc_now_iso()
        updated = item.model_copy(
            update={
                "status": CommandQueueStatus.CANCELLED,
                "updated_at": now,
                "cancelled_at": now,
                "terminal_at": now,
                "reason_code": reason_code,
            }
        )
        self._items[queue_id] = updated
        return updated

    def mark_applied(
        self,
        queue_id: str,
        *,
        claimant_id: str | None = None,
        applied_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem | None:
        """Mark a queued command as applied."""
        with self._lock:
            item = self._items.get(queue_id)
            if item is None:
                return None
            if item.status is not CommandQueueStatus.QUEUED:
                return item
            if claimant_id is not None and item.claimed_by not in (None, claimant_id):
                return item
            resolved = item.resolved_semantic
            reason = (
                "stop_applied"
                if item.requested_semantic is LiveMessageSemantic.STOP
                else "applied"
            )
            if (
                item.requested_semantic is LiveMessageSemantic.REDIRECT_CURRENT
                and applied_phase is not LiveMessagePhase.LLM_IN_FLIGHT
            ):
                resolved = LiveMessageSemantic.STEER_CURRENT
                reason = "redirect_degraded_tool_phase"
            now = utc_now_iso()
            updated = item.model_copy(
                update={
                    "status": CommandQueueStatus.APPLIED,
                    "updated_at": now,
                    "applied_at": now,
                    "terminal_at": now,
                    "applied_phase": applied_phase,
                    "resolved_semantic": resolved,
                    "reason_code": reason,
                    "claimed_by": None,
                    "claimed_at": None,
                }
            )
            self._items[queue_id] = updated
            return updated

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
        """Mark a queued command as failed."""
        with self._lock:
            item = self._items.get(queue_id)
            if item is None:
                return None
            if item.status is not CommandQueueStatus.QUEUED:
                return item
            now = utc_now_iso()
            updated = item.model_copy(
                update={
                    "status": CommandQueueStatus.FAILED,
                    "updated_at": now,
                    "failed_at": now,
                    "terminal_at": now,
                    "error": error,
                    "reason_code": error,
                    "claimed_by": None,
                    "claimed_at": None,
                }
            )
            self._items[queue_id] = updated
            return updated

    def set_run_phase(
        self,
        run_id: str,
        phase: LiveMessagePhase,
        *,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> LiveRunState:
        with self._lock:
            prior = self._run_states.get(run_id)
            if prior is not None and prior.phase is LiveMessagePhase.TERMINAL:
                return prior
            state = LiveRunState(
                run_id=run_id,
                thread_id=thread_id or (prior.thread_id if prior else None),
                agent_id=agent_id or (prior.agent_id if prior else None),
                phase=phase,
                llm_generation=prior.llm_generation if prior else 0,
                stopped=prior.stopped if prior else False,
            )
            self._run_states[run_id] = state
            return state

    def get_run_state(self, run_id: str) -> LiveRunState | None:
        with self._lock:
            return self._run_states.get(run_id)

    def current_llm_generation(self, run_id: str) -> int:
        state = self.get_run_state(run_id)
        return state.llm_generation if state is not None else 0

    def claim_for_boundary(
        self,
        *,
        run_id: str,
        claimant_id: str,
        applied_phase: LiveMessagePhase,
    ) -> CommandQueueItem | None:
        with self._lock:
            state = self._run_states.get(run_id)
            if state is not None and state.phase is LiveMessagePhase.TERMINAL:
                return None
            for item in self.list_pending(run_id=run_id):
                if item.claimed_by is not None:
                    continue
                if state is not None and state.stopped:
                    if item.requested_semantic is not LiveMessageSemantic.STOP:
                        continue
                elif item.requested_semantic is LiveMessageSemantic.STOP:
                    pass
                if item.requested_semantic is LiveMessageSemantic.QUEUE_NEXT:
                    continue
                if item.priority is ControlPriority.LATER:
                    continue
                now = utc_now_iso()
                claimed = item.model_copy(
                    update={"claimed_by": claimant_id, "claimed_at": now, "updated_at": now}
                )
                self._items[item.queue_id] = claimed
                return claimed
            return None

    def claim_hard_redirect(
        self, *, run_id: str, claimant_id: str, expected_generation: int
    ) -> CommandQueueItem | None:
        with self._lock:
            state = self._run_states.get(run_id)
            if (
                state is None
                or state.stopped
                or state.phase is not LiveMessagePhase.LLM_IN_FLIGHT
                or state.llm_generation != expected_generation
            ):
                return None
            for item in self.list_pending(run_id=run_id):
                if (
                    item.requested_semantic is not LiveMessageSemantic.REDIRECT_CURRENT
                    or item.claimed_by is not None
                ):
                    continue
                now = utc_now_iso()
                next_generation = state.llm_generation + 1
                state = state.model_copy(
                    update={"llm_generation": next_generation, "updated_at": now}
                )
                self._run_states[run_id] = state
                claimed = item.model_copy(
                    update={
                        "claimed_by": claimant_id,
                        "claimed_at": now,
                        "updated_at": now,
                        "applied_phase": LiveMessagePhase.LLM_IN_FLIGHT,
                        "resolved_semantic": LiveMessageSemantic.REDIRECT_CURRENT,
                        "reason_code": "redirect_claimed",
                        "superseded_generation": expected_generation,
                        "llm_generation": next_generation,
                    }
                )
                self._items[item.queue_id] = claimed
                return claimed
            return None

    def release_claim(
        self, queue_id: str, *, claimant_id: str
    ) -> CommandQueueItem | None:
        with self._lock:
            item = self._items.get(queue_id)
            if item is None or item.status is not CommandQueueStatus.QUEUED:
                return item
            if item.claimed_by != claimant_id:
                return item
            updated = item.model_copy(
                update={"claimed_by": None, "claimed_at": None, "updated_at": utc_now_iso()}
            )
            self._items[queue_id] = updated
            return updated

    def commit_terminal(
        self, run_id: str, *, stopped: bool = False
    ) -> list[CommandQueueItem]:
        with self._lock:
            prior = self._run_states.get(run_id) or LiveRunState(run_id=run_id)
            now = utc_now_iso()
            self._run_states[run_id] = prior.model_copy(
                update={
                    "phase": LiveMessagePhase.TERMINAL,
                    "stopped": stopped or prior.stopped,
                    "terminal_at": prior.terminal_at or now,
                    "updated_at": now,
                }
            )
            changed: list[CommandQueueItem] = []
            for item in self.list_pending(run_id=run_id):
                if item.requested_semantic is LiveMessageSemantic.STOP:
                    if stopped or prior.stopped:
                        applied_stop = self.mark_applied(
                            item.queue_id,
                            applied_phase=prior.phase,
                        )
                        if applied_stop is not None:
                            changed.append(applied_stop)
                    continue
                if item.requested_semantic not in (
                    LiveMessageSemantic.STEER_CURRENT,
                    LiveMessageSemantic.REDIRECT_CURRENT,
                    LiveMessageSemantic.QUEUE_NEXT,
                ):
                    continue
                if stopped or prior.stopped:
                    failed = self.mark_failed(item.queue_id, error="run_stopped")
                    if failed is not None:
                        changed.append(failed)
                    continue
                if item.requested_semantic is LiveMessageSemantic.QUEUE_NEXT:
                    continue
                promoted = item.model_copy(
                    update={
                        "priority": ControlPriority.NEXT,
                        "resolved_semantic": LiveMessageSemantic.QUEUE_NEXT,
                        "applies_at": "after_source_terminal",
                        "reason_code": "terminal_promoted_to_next",
                        "handoff_id": item.handoff_id or _handoff_id(item.queue_id),
                        "claimed_by": None,
                        "claimed_at": None,
                        "updated_at": now,
                    }
                )
                self._items[item.queue_id] = promoted
                changed.append(promoted)
            return changed

    def stop_run(self, run_id: str) -> list[CommandQueueItem]:
        return self.commit_terminal(run_id, stopped=True)

    def cancel_next(self, queue_id: str) -> CommandQueueItem | None:
        with self._lock:
            item = self._items.get(queue_id)
            if item is None or item.status is not CommandQueueStatus.QUEUED:
                return item
            if item.resolved_semantic is not LiveMessageSemantic.QUEUE_NEXT:
                return item
            return self._cancel_unlocked(
                queue_id, reason_code="cancelled_by_operator"
            )

    def claim_next_handoff(
        self, *, source_run_id: str, claimant_id: str
    ) -> CommandQueueItem | None:
        with self._lock:
            state = self._run_states.get(source_run_id)
            if state is None or state.phase is not LiveMessagePhase.TERMINAL or state.stopped:
                return None
            for item in self.list_pending(run_id=source_run_id):
                if (
                    item.resolved_semantic is not LiveMessageSemantic.QUEUE_NEXT
                    or item.claimed_by not in (None, claimant_id)
                ):
                    continue
                if item.claimed_by == claimant_id:
                    return item
                now = utc_now_iso()
                claimed = item.model_copy(
                    update={
                        "claimed_by": claimant_id,
                        "claimed_at": now,
                        "updated_at": now,
                        "handoff_id": item.handoff_id or _handoff_id(item.queue_id),
                    }
                )
                self._items[item.queue_id] = claimed
                return claimed
            return None

    def complete_handoff(
        self,
        queue_id: str,
        *,
        claimant_id: str,
        destination_turn_id: str,
    ) -> CommandQueueItem | None:
        with self._lock:
            item = self._items.get(queue_id)
            if item is None or item.status is not CommandQueueStatus.QUEUED:
                return item
            if item.claimed_by != claimant_id:
                return item
            now = utc_now_iso()
            updated = item.model_copy(
                update={
                    "status": CommandQueueStatus.APPLIED,
                    "destination_turn_id": destination_turn_id,
                    "applied_phase": LiveMessagePhase.TERMINAL,
                    "applied_at": now,
                    "terminal_at": now,
                    "updated_at": now,
                    "reason_code": "next_handoff_completed",
                    "claimed_by": None,
                    "claimed_at": None,
                }
            )
            self._items[queue_id] = updated
            return updated

    def contract_schema_version(self) -> int:
        """Return the live-message state-machine schema understood by this store."""
        return 1

    def quarantine_legacy_rows(self) -> list[CommandQueueItem]:
        """Fail ambiguous pre-v1 NEXT rows before they can be dispatched."""
        with self._lock:
            changed: list[CommandQueueItem] = []
            for queue_id in list(self._order):
                item = self._items[queue_id]
                if (
                    item.schema_version == 0
                    and item.status is CommandQueueStatus.QUEUED
                    and item.kind in (
                        ControlKind.ENQUEUE_USER_MESSAGE,
                        ControlKind.REDIRECT_USER_MESSAGE,
                    )
                    and item.priority is ControlPriority.NEXT
                ):
                    failed = self.mark_failed(queue_id, error="legacy_unresolved")
                    if failed is not None:
                        changed.append(failed)
            return changed

    def _dedupe_match(self, request: ControlRequest) -> CommandQueueItem | None:
        if not request.dedupe_key:
            return None
        request_hash = control_request_sha256(request)
        for queue_id in self._order:
            item = self._items[queue_id]
            if (
                item.kind == request.kind
                and item.source == request.source
                and item.dedupe_key == request.dedupe_key
                and item.run_id == request.run_id
                and item.thread_id == request.thread_id
                and item.agent_id == request.agent_id
            ):
                if item.request_sha256 != request_hash:
                    raise LiveMessageIdempotencyError()
                return item
        return None


def _handoff_id(queue_id: str) -> str:
    return f"handoff_{queue_id.removeprefix('cmd_')}"


def _dispatch_order(item: CommandQueueItem) -> int:
    if item.kind is ControlKind.INTERRUPT:
        return 0
    if item.requested_semantic is LiveMessageSemantic.REDIRECT_CURRENT:
        return 1
    if item.requested_semantic is LiveMessageSemantic.STEER_CURRENT:
        return 2
    if item.requested_semantic is LiveMessageSemantic.QUEUE_NEXT:
        return 11
    return 10 + _PRIORITY_ORDER[item.priority]


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
