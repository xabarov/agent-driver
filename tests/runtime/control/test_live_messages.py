"""Generic live-message semantics for current-turn steering and NEXT handoff."""

from __future__ import annotations

from collections import Counter

import pytest

from agent_driver.contracts import (
    CommandQueueStatus,
    ControlKind,
    ControlPriority,
    ControlRequest,
    LiveMessageAdmissionError,
    LiveMessagePhase,
    LiveMessageSemantic,
)
from agent_driver.runtime import (
    InMemoryCommandQueueStore,
    dispatch_next_turn,
    live_message_capabilities,
)


def _request(
    kind: ControlKind,
    priority: ControlPriority,
    text: str,
    *,
    run_id: str = "run-live",
    dedupe_key: str | None = None,
) -> ControlRequest:
    return ControlRequest(
        kind=kind,
        priority=priority,
        run_id=run_id,
        payload={"message": text},
        source="contract-test",
        dedupe_key=dedupe_key,
    )


def test_requested_semantics_are_explicit_and_content_is_hashed() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)

    steer = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "steer")
    )
    redirect = store.admit(
        _request(ControlKind.REDIRECT_USER_MESSAGE, ControlPriority.NOW, "redirect")
    )
    queued = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "next")
    )

    assert steer.requested_semantic is LiveMessageSemantic.STEER_CURRENT
    assert redirect.requested_semantic is LiveMessageSemantic.REDIRECT_CURRENT
    assert queued.requested_semantic is LiveMessageSemantic.QUEUE_NEXT
    assert steer.sequence < redirect.sequence < queued.sequence
    assert len(steer.content_sha256 or "") == 64
    assert steer.accepted_phase is LiveMessagePhase.LLM_IN_FLIGHT
    assert steer.schema_version == 1


def test_step_boundary_applies_now_but_never_drains_next() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.TOOL_IN_FLIGHT)
    steer = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "steer")
    )
    redirect = store.admit(
        _request(ControlKind.REDIRECT_USER_MESSAGE, ControlPriority.NOW, "urgent")
    )
    queued = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "later")
    )

    first = store.claim_for_boundary(
        run_id="run-live",
        claimant_id="runner-a",
        applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
    )
    assert first is not None
    store.mark_applied(
        first.queue_id,
        claimant_id="runner-a",
        applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
    )
    second = store.claim_for_boundary(
        run_id="run-live",
        claimant_id="runner-a",
        applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
    )
    assert second is not None
    degraded = store.mark_applied(
        second.queue_id,
        claimant_id="runner-a",
        applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
    )

    assert {first.queue_id, second.queue_id} == {steer.queue_id, redirect.queue_id}
    assert degraded is not None
    if degraded.queue_id == redirect.queue_id:
        assert degraded.resolved_semantic is LiveMessageSemantic.STEER_CURRENT
        assert degraded.reason_code == "redirect_degraded_tool_phase"
    assert (
        store.claim_for_boundary(
            run_id="run-live",
            claimant_id="runner-a",
            applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
        )
        is None
    )
    assert store.get(queued.queue_id).status is CommandQueueStatus.QUEUED


def test_terminal_race_promotes_accepted_now_and_rejects_late_admission() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.FINALIZING)
    accepted = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "keep me")
    )

    promoted = store.commit_terminal("run-live")
    receipt = store.get(accepted.queue_id)

    assert [item.queue_id for item in promoted] == [accepted.queue_id]
    assert receipt is not None
    assert receipt.priority is ControlPriority.NEXT
    assert receipt.requested_semantic is LiveMessageSemantic.STEER_CURRENT
    assert receipt.resolved_semantic is LiveMessageSemantic.QUEUE_NEXT
    assert receipt.reason_code == "terminal_promoted_to_next"
    assert receipt.handoff_id

    with pytest.raises(LiveMessageAdmissionError) as exc:
        store.admit(
            _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "too late")
        )
    assert exc.value.reason_code == "turn_no_longer_steerable"


def test_stop_preempts_every_pending_message() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)
    rows = [
        store.admit(
            _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "steer")
        ),
        store.admit(
            _request(ControlKind.REDIRECT_USER_MESSAGE, ControlPriority.NOW, "redirect")
        ),
        store.admit(
            _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "next")
        ),
    ]

    stopped = store.stop_run("run-live")

    assert {item.queue_id for item in stopped} == {item.queue_id for item in rows}
    assert all(item.status is CommandQueueStatus.FAILED for item in stopped)
    assert all(item.reason_code == "run_stopped" for item in stopped)
    assert (
        store.claim_for_boundary(
            run_id="run-live",
            claimant_id="runner",
            applied_phase=LiveMessagePhase.LLM_IN_FLIGHT,
        )
        is None
    )


def test_stop_admission_is_the_preemption_boundary() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)
    steer = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "steer")
    )
    queued = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "next")
    )
    stop = store.admit(
        ControlRequest(
            kind=ControlKind.INTERRUPT,
            priority=ControlPriority.NOW,
            run_id="run-live",
            payload={"reason": "operator_stop"},
            source="contract-test",
        )
    )

    assert store.get_run_state("run-live").stopped is True
    assert store.get(steer.queue_id).reason_code == "run_stopped"
    assert store.get(queued.queue_id).reason_code == "run_stopped"
    claimed = store.claim_for_boundary(
        run_id="run-live",
        claimant_id="runner",
        applied_phase=LiveMessagePhase.LLM_IN_FLIGHT,
    )
    assert claimed is not None
    assert claimed.queue_id == stop.queue_id


def test_next_handoff_replay_uses_same_stable_claimant_without_release() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)
    queued = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "next")
    )
    store.commit_terminal("run-live")
    destinations: dict[str, str] = {}

    def create(handoff):
        return destinations.setdefault(handoff.handoff_id, "turn-next-stable")

    assert (
        dispatch_next_turn(
            store=store,
            source_run_id="run-live",
            claimant_id="stable-dispatcher",
            create_next_turn=create,
            crash_after_host=True,
        )
        is None
    )
    applied = dispatch_next_turn(
        store=store,
        source_run_id="run-live",
        claimant_id="stable-dispatcher",
        create_next_turn=create,
    )

    assert applied is not None
    assert applied.queue_id == queued.queue_id
    assert applied.destination_turn_id == "turn-next-stable"
    assert len(destinations) == 1


def test_hard_redirect_claim_advances_one_generation_at_most_once() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)
    queued = store.admit(
        _request(ControlKind.REDIRECT_USER_MESSAGE, ControlPriority.NOW, "urgent")
    )

    claimed = store.claim_hard_redirect(
        run_id="run-live", claimant_id="runner-a", expected_generation=0
    )

    assert claimed is not None
    assert claimed.queue_id == queued.queue_id
    assert claimed.superseded_generation == 0
    assert claimed.llm_generation == 1
    assert store.current_llm_generation("run-live") == 1
    assert (
        store.claim_hard_redirect(
            run_id="run-live", claimant_id="runner-b", expected_generation=0
        )
        is None
    )


def test_next_handoff_retries_same_identity_and_applies_once() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)
    queued = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "next turn")
    )
    store.commit_terminal("run-live")
    host_calls: Counter[str] = Counter()
    destinations: dict[str, str] = {}

    def create_next_turn(handoff):
        host_calls[handoff.handoff_id] += 1
        return destinations.setdefault(handoff.handoff_id, "turn-next")

    first = dispatch_next_turn(
        store=store,
        source_run_id="run-live",
        claimant_id="dispatcher-a",
        create_next_turn=create_next_turn,
        crash_after_host=True,
    )
    assert first is None

    store.release_claim(queued.queue_id, claimant_id="dispatcher-a")
    applied = dispatch_next_turn(
        store=store,
        source_run_id="run-live",
        claimant_id="dispatcher-b",
        create_next_turn=create_next_turn,
    )

    assert applied is not None
    assert applied.destination_turn_id == "turn-next"
    assert applied.status is CommandQueueStatus.APPLIED
    assert host_calls[applied.handoff_id] == 2
    assert len(destinations) == 1
    assert (
        dispatch_next_turn(
            store=store,
            source_run_id="run-live",
            claimant_id="dispatcher-c",
            create_next_turn=create_next_turn,
        )
        is None
    )


def test_cancel_only_removes_pending_next_and_dedupe_is_verbatim() -> None:
    store = InMemoryCommandQueueStore()
    store.set_run_phase("run-live", LiveMessagePhase.LLM_IN_FLIGHT)
    first = store.admit(
        _request(
            ControlKind.ENQUEUE_USER_MESSAGE,
            ControlPriority.NEXT,
            "next",
            dedupe_key="same",
        )
    )
    replay = store.admit(
        _request(
            ControlKind.ENQUEUE_USER_MESSAGE,
            ControlPriority.NEXT,
            "next",
            dedupe_key="same",
        )
    )
    assert replay == first

    cancelled = store.cancel_next(first.queue_id)
    assert cancelled is not None
    assert cancelled.status is CommandQueueStatus.CANCELLED
    assert cancelled.reason_code == "cancelled_by_operator"
    assert store.cancel_next(first.queue_id) == cancelled


def test_capability_manifest_is_versioned_and_store_aware() -> None:
    capability = live_message_capabilities(
        InMemoryCommandQueueStore(), durable_store="memory"
    )
    assert capability.schema_id == "agent-driver.live-message-controls.v1"
    assert capability.model_dump(by_alias=True)["schema"] == (
        "agent-driver.live-message-controls.v1"
    )
    assert capability.contract_version == 1
    assert capability.soft_steer is True
    assert capability.hard_redirect is True
    assert capability.queue_next is True
    assert capability.cancel_queued is True
    assert capability.durable_store == "memory"
