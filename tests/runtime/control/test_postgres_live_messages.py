"""Real-Postgres races and recreate proof for live-message contract v1."""

from __future__ import annotations

import threading
from collections import Counter

import pytest

from agent_driver.contracts import (
    CommandQueueItem,
    CommandQueueStatus,
    ControlKind,
    ControlPriority,
    ControlRequest,
    LiveMessageAdmissionError,
    LiveMessagePhase,
    LiveMessageSemantic,
)
from agent_driver.runtime import PostgresCommandQueueStore, dispatch_next_turn

pytestmark = pytest.mark.postgres


def _request(
    kind: ControlKind,
    priority: ControlPriority,
    text: str,
    *,
    dedupe_key: str | None = None,
) -> ControlRequest:
    return ControlRequest(
        kind=kind,
        run_id="run-pg-live",
        priority=priority,
        payload={"message": text},
        source="pg-live-test",
        dedupe_key=dedupe_key,
    )


def test_pg_live_admission_dedupe_and_recreate(pg_control_config) -> None:
    first = PostgresCommandQueueStore(config=pg_control_config)
    first.set_run_phase("run-pg-live", LiveMessagePhase.LLM_IN_FLIGHT)
    accepted = first.admit(
        _request(
            ControlKind.ENQUEUE_USER_MESSAGE,
            ControlPriority.NOW,
            "steer",
            dedupe_key="same",
        )
    )

    reopened = PostgresCommandQueueStore(config=pg_control_config)
    replay = reopened.admit(
        _request(
            ControlKind.ENQUEUE_USER_MESSAGE,
            ControlPriority.NOW,
            "steer",
            dedupe_key="same",
        )
    )

    assert replay.queue_id == accepted.queue_id
    assert replay.sequence == accepted.sequence
    assert reopened.get_run_state("run-pg-live").phase is LiveMessagePhase.LLM_IN_FLIGHT


def test_pg_two_claimers_apply_one_boundary_message(pg_control_config) -> None:
    seed = PostgresCommandQueueStore(config=pg_control_config)
    seed.set_run_phase("run-pg-live", LiveMessagePhase.TOOL_IN_FLIGHT)
    queued = seed.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "steer")
    )
    claims = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def worker(name: str) -> None:
        store = PostgresCommandQueueStore(config=pg_control_config)
        barrier.wait()
        claimed = store.claim_for_boundary(
            run_id="run-pg-live",
            claimant_id=name,
            applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
        )
        with lock:
            claims.append(claimed)

    threads = [threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.queue_id == queued.queue_id
    applied = seed.mark_applied(
        queued.queue_id,
        claimant_id=winner.claimed_by,
        applied_phase=LiveMessagePhase.TOOL_IN_FLIGHT,
    )
    assert applied.status is CommandQueueStatus.APPLIED


def test_pg_terminal_promotion_and_late_rejection_are_atomic(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    store.set_run_phase("run-pg-live", LiveMessagePhase.FINALIZING)
    accepted = store.admit(
        _request(ControlKind.REDIRECT_USER_MESSAGE, ControlPriority.NOW, "urgent")
    )
    store.commit_terminal("run-pg-live")

    promoted = store.get(accepted.queue_id)
    assert promoted.resolved_semantic is LiveMessageSemantic.QUEUE_NEXT
    assert promoted.reason_code == "terminal_promoted_to_next"
    assert promoted.handoff_id
    with pytest.raises(LiveMessageAdmissionError) as exc:
        store.admit(
            _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "late")
        )
    assert exc.value.reason_code == "turn_no_longer_steerable"


def test_pg_stop_preempts_all_semantics(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    store.set_run_phase("run-pg-live", LiveMessagePhase.LLM_IN_FLIGHT)
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

    stopped = store.stop_run("run-pg-live")

    assert {item.queue_id for item in stopped} == {item.queue_id for item in rows}
    assert all(item.status is CommandQueueStatus.FAILED for item in stopped)
    assert all(item.reason_code == "run_stopped" for item in stopped)


def test_pg_stop_admission_preempts_before_dispatch(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    store.set_run_phase("run-pg-live", LiveMessagePhase.LLM_IN_FLIGHT)
    pending = store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NOW, "steer")
    )
    stop = store.admit(
        ControlRequest(
            kind=ControlKind.INTERRUPT,
            run_id="run-pg-live",
            priority=ControlPriority.NOW,
            payload={"reason": "operator_stop"},
            source="pg-live-test",
        )
    )

    reopened = PostgresCommandQueueStore(config=pg_control_config)
    assert reopened.get_run_state("run-pg-live").stopped is True
    assert reopened.get(pending.queue_id).reason_code == "run_stopped"
    claimed = reopened.claim_for_boundary(
        run_id="run-pg-live",
        claimant_id="runner",
        applied_phase=LiveMessagePhase.LLM_IN_FLIGHT,
    )
    assert claimed is not None
    assert claimed.queue_id == stop.queue_id


def test_pg_redirect_generation_compare_and_swap(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    store.set_run_phase("run-pg-live", LiveMessagePhase.LLM_IN_FLIGHT)
    queued = store.admit(
        _request(ControlKind.REDIRECT_USER_MESSAGE, ControlPriority.NOW, "urgent")
    )
    claims = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def worker(name: str) -> None:
        local = PostgresCommandQueueStore(config=pg_control_config)
        barrier.wait()
        result = local.claim_hard_redirect(
            run_id="run-pg-live", claimant_id=name, expected_generation=0
        )
        with lock:
            claims.append(result)

    threads = [
        threading.Thread(target=worker, args=(f"redirect-{i}",)) for i in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    assert winners[0].queue_id == queued.queue_id
    assert winners[0].superseded_generation == 0
    assert winners[0].llm_generation == 1
    assert store.current_llm_generation("run-pg-live") == 1


def test_pg_next_handoff_crash_retry_uses_one_identity(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    store.set_run_phase("run-pg-live", LiveMessagePhase.LLM_IN_FLIGHT)
    store.admit(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, ControlPriority.NEXT, "next")
    )
    store.commit_terminal("run-pg-live")
    calls: Counter[str] = Counter()
    destinations: dict[str, str] = {}

    def create(handoff):
        calls[handoff.handoff_id] += 1
        return destinations.setdefault(handoff.handoff_id, "turn-pg-next")

    assert (
        dispatch_next_turn(
            store=store,
            source_run_id="run-pg-live",
            claimant_id="dispatcher-a",
            create_next_turn=create,
            crash_after_host=True,
        )
        is None
    )
    applied = dispatch_next_turn(
        store=PostgresCommandQueueStore(config=pg_control_config),
        source_run_id="run-pg-live",
        claimant_id="dispatcher-a",
        create_next_turn=create,
    )

    assert applied is not None
    assert applied.destination_turn_id == "turn-pg-next"
    assert applied.status is CommandQueueStatus.APPLIED
    assert calls[applied.handoff_id] == 2
    assert len(destinations) == 1


def test_pg_legacy_ambiguous_next_is_quarantined_with_payload_preserved(
    pg_control_config,
) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    legacy = CommandQueueItem(
        queue_id="cmd_legacy_next",
        control_id="ctrl_legacy_next",
        run_id="run-pg-live",
        kind=ControlKind.ENQUEUE_USER_MESSAGE,
        priority=ControlPriority.NEXT,
        payload={"message": "legacy operator text"},
        source="pre-v1",
    )
    with store._connect(autocommit=True) as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {store._table} (
                    queue_id, control_id, run_id, thread_id, agent_id, priority,
                    kind, status, source, dedupe_key, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,  # noqa: S608 - disposable schema name comes from the fixture
                (
                    legacy.queue_id,
                    legacy.control_id,
                    legacy.run_id,
                    legacy.thread_id,
                    legacy.agent_id,
                    legacy.priority.value,
                    legacy.kind.value,
                    legacy.status.value,
                    legacy.source,
                    legacy.dedupe_key,
                    legacy.created_at,
                    legacy.model_dump_json(),
                ),
            )

    changed = store.quarantine_legacy_rows()
    readback = store.get(legacy.queue_id)

    assert [item.queue_id for item in changed] == [legacy.queue_id]
    assert readback is not None
    assert readback.status is CommandQueueStatus.FAILED
    assert readback.reason_code == "legacy_unresolved"
    assert readback.payload == {"message": "legacy operator text"}
