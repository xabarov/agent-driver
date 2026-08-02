"""Real-Postgres acceptance matrix for the durable control plane (R2 — epic 058).

Every test here needs a live Postgres (the ``pg_control_config`` fixture, gated by
``AD_REQUIRE_POSTGRES``/``AD_POSTGRES_TEST_DSN``); the whole module carries the
``postgres`` marker so it is excluded from the default sweep and runs only in the
mandatory postgres CI job. These prove the transactional / unique-constraint / CAS
behaviour of the real backend a multi-worker product coordinates through — not just
the algorithm on a single-file SQLite store.
"""

from __future__ import annotations

import threading
from collections import Counter

import pytest

from agent_driver.contracts.context import PlanArtifact
from agent_driver.contracts.control import ControlKind, ControlPriority, ControlRequest
from agent_driver.contracts.enums import PlanningModeState
from agent_driver.runtime.control import (
    ApprovalConsumeRequest,
    ConsumeStatus,
    InMemoryApprovalConsumptionStore,
    PostgresAbortLifecycleStore,
    PostgresApprovalConsumptionStore,
    PostgresCommandQueueStore,
    PostgresPlanArtifactStore,
    SqliteApprovalConsumptionStore,
)
from agent_driver.runtime.control.abort_store import AbortLifecycleState

pytestmark = pytest.mark.postgres


def _plan(plan_id: str, run_id: str, content: str = "step 1") -> PlanArtifact:
    return PlanArtifact(
        plan_id=plan_id,
        run_id=run_id,
        agent_id="agent",
        content=content,
        content_hash="h",
        status=PlanningModeState.COLLECTING,
        created_at="2026-08-03T00:00:00Z",
        updated_at="2026-08-03T00:00:00Z",
    )


# --------------------------------------------------------------------------- #
# Approval consumption — exactly-once CAS, replay, conflict, two-client race
# --------------------------------------------------------------------------- #


def test_pg_approval_consume_then_duplicate(pg_control_config) -> None:
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    req = ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="approve")
    first = store.try_consume(req)
    second = store.try_consume(req)
    assert first.status is ConsumeStatus.CONSUMED
    assert first.is_first
    assert second.status is ConsumeStatus.DUPLICATE
    assert second.prior_decision == "approve"


def test_pg_approval_conflict_on_different_decision(pg_control_config) -> None:
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    store.try_consume(
        ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="approve")
    )
    conflict = store.try_consume(
        ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="reject")
    )
    assert conflict.status is ConsumeStatus.CONFLICT
    assert conflict.prior_decision == "approve"


def test_pg_approval_idempotency_key_loses_cas(pg_control_config) -> None:
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    store.try_consume(
        ApprovalConsumeRequest(
            run_id="r1", interrupt_id="i1", decision="approve", idempotency_key="k1"
        )
    )
    # A different interrupt carrying the same key must also lose the swap.
    dup = store.try_consume(
        ApprovalConsumeRequest(
            run_id="r1", interrupt_id="i2", decision="approve", idempotency_key="k1"
        )
    )
    assert dup.status is ConsumeStatus.DUPLICATE
    assert dup.prior_decision == "approve"


def test_pg_approval_crash_safe_replay(pg_control_config) -> None:
    """Row is written before the tool runs; a duplicate replays the recorded result."""
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    req = ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="approve")
    assert store.try_consume(req).status is ConsumeStatus.CONSUMED
    # Simulated crash between consume and result: a duplicate must NOT re-run the
    # tool and has no result yet to replay.
    mid = store.try_consume(req)
    assert mid.status is ConsumeStatus.DUPLICATE
    assert mid.prior_result_payload is None
    # After the tool completes and the result is recorded, duplicates replay it.
    store.record_result(
        run_id="r1", interrupt_id="i1", result_ref="ref-1", result_payload='{"ok":1}'
    )
    replay = store.try_consume(req)
    assert replay.status is ConsumeStatus.DUPLICATE
    assert replay.prior_result_ref == "ref-1"
    assert replay.prior_result_payload == '{"ok":1}'


def test_pg_approval_two_client_race_single_winner(pg_control_config) -> None:
    """16 concurrent clients consume one interrupt — exactly one wins the CAS."""
    store = PostgresApprovalConsumptionStore(config=pg_control_config)
    req = ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="approve")
    outcomes: list[ConsumeStatus] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def worker() -> None:
        barrier.wait()
        outcome = store.try_consume(req)
        with lock:
            outcomes.append(outcome.status)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = Counter(outcomes)
    assert counts[ConsumeStatus.CONSUMED] == 1, counts
    assert counts[ConsumeStatus.DUPLICATE] == 15, counts


def test_pg_approval_survives_restart(pg_control_config) -> None:
    """A fresh store instance on the same schema sees the committed consume."""
    a = PostgresApprovalConsumptionStore(config=pg_control_config)
    a.try_consume(
        ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="approve")
    )
    b = PostgresApprovalConsumptionStore(config=pg_control_config)
    got = b.get(run_id="r1", interrupt_id="i1")
    assert got is not None
    assert got.prior_decision == "approve"


# --------------------------------------------------------------------------- #
# Abort lifecycle — durable states, monotonic CAS, concurrency
# --------------------------------------------------------------------------- #


def test_pg_abort_full_lifecycle(pg_control_config) -> None:
    store = PostgresAbortLifecycleStore(config=pg_control_config)
    requested = store.request_abort("r1", reason="user", actor="op")
    assert requested.state is AbortLifecycleState.REQUESTED
    assert requested.observed is False
    observed = store.mark_observed("r1")
    assert observed.state is AbortLifecycleState.OBSERVED
    assert observed.observed is True
    resolved = store.resolve("r1", cancelled=True)
    assert resolved is not None
    assert resolved.state is AbortLifecycleState.CANCELLED
    assert resolved.is_terminal


def test_pg_abort_completed_before_cancel(pg_control_config) -> None:
    store = PostgresAbortLifecycleStore(config=pg_control_config)
    store.request_abort("r1")
    resolved = store.resolve("r1", cancelled=False)
    assert resolved is not None
    assert resolved.state is AbortLifecycleState.COMPLETED_BEFORE_CANCEL


def test_pg_abort_terminal_not_reversed(pg_control_config) -> None:
    store = PostgresAbortLifecycleStore(config=pg_control_config)
    store.request_abort("r1")
    store.resolve("r1", cancelled=True)
    # An observation landing after the terminal state must not move it back.
    after = store.mark_observed("r1")
    assert after.state is AbortLifecycleState.CANCELLED


def test_pg_abort_resolve_unknown_run_is_none(pg_control_config) -> None:
    store = PostgresAbortLifecycleStore(config=pg_control_config)
    assert store.resolve("never", cancelled=True) is None


def test_pg_abort_concurrent_request_single_row(pg_control_config) -> None:
    store = PostgresAbortLifecycleStore(config=pg_control_config)
    barrier = threading.Barrier(12)

    def worker() -> None:
        barrier.wait()
        store.request_abort("r1", reason="user")

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    record = store.get("r1")
    assert record is not None
    assert record.state is AbortLifecycleState.REQUESTED


# --------------------------------------------------------------------------- #
# Plan artifacts — durable persistence + restart readback
# --------------------------------------------------------------------------- #


def test_pg_plan_put_get_list_and_upsert(pg_control_config) -> None:
    store = PostgresPlanArtifactStore(config=pg_control_config)
    store.put(_plan("p1", "r1", content="v1"))
    store.put(_plan("p2", "r1", content="v2"))
    assert store.get("p1").content == "v1"
    listed = store.list_for_run("r1")
    assert [p.plan_id for p in listed] == ["p1", "p2"]
    # Upsert replaces the payload for the same plan_id.
    store.put(_plan("p1", "r1", content="v1-edited"))
    assert store.get("p1").content == "v1-edited"
    assert len(store.list_for_run("r1")) == 2


def test_pg_plan_survives_restart(pg_control_config) -> None:
    PostgresPlanArtifactStore(config=pg_control_config).put(_plan("p1", "r1"))
    reopened = PostgresPlanArtifactStore(config=pg_control_config)
    got = reopened.get("p1")
    assert got is not None
    assert got.run_id == "r1"


# --------------------------------------------------------------------------- #
# Command queue — enqueue/dedupe/lifecycle, durable readback
# --------------------------------------------------------------------------- #


def _control_request(dedupe_key: str | None = None) -> ControlRequest:
    return ControlRequest(
        kind=ControlKind.ENQUEUE_USER_MESSAGE,
        source="test",
        run_id="r1",
        payload={"text": "focus"},
        priority=ControlPriority.NOW,
        dedupe_key=dedupe_key,
    )


def test_pg_queue_enqueue_get_and_dedupe(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    item = store.enqueue(_control_request(dedupe_key="d1"))
    assert store.get(item.queue_id).queue_id == item.queue_id
    # Same dedupe key returns the existing pending item, not a new one.
    again = store.enqueue(_control_request(dedupe_key="d1"))
    assert again.queue_id == item.queue_id
    assert len(store.list_pending(run_id="r1")) == 1


def test_pg_queue_lifecycle_transitions(pg_control_config) -> None:
    store = PostgresCommandQueueStore(config=pg_control_config)
    a = store.enqueue(_control_request())
    applied = store.mark_applied(a.queue_id)
    assert applied.status.value == "applied"
    b = store.enqueue(_control_request())
    failed = store.mark_failed(b.queue_id, error="boom")
    assert failed.status.value == "failed"
    assert failed.error == "boom"
    c = store.enqueue(_control_request())
    cancelled = store.cancel(c.queue_id)
    assert cancelled.status.value == "cancelled"
    # Only the still-queued items remain pending.
    assert store.list_pending(run_id="r1") == []


def test_pg_queue_survives_restart(pg_control_config) -> None:
    item = PostgresCommandQueueStore(config=pg_control_config).enqueue(
        _control_request()
    )
    reopened = PostgresCommandQueueStore(config=pg_control_config)
    assert reopened.get(item.queue_id) is not None


# --------------------------------------------------------------------------- #
# Cross-backend parity — same outcomes on in-memory, SQLite and Postgres
# --------------------------------------------------------------------------- #


def test_backend_parity_approval(pg_control_config, tmp_path) -> None:
    """The approval CAS outcome sequence is identical across all three backends."""
    backends = {
        "in_memory": InMemoryApprovalConsumptionStore(),
        "sqlite": SqliteApprovalConsumptionStore(
            path=str(tmp_path / "approval.db")
        ),
        "postgres": PostgresApprovalConsumptionStore(config=pg_control_config),
    }
    req = ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="approve")
    other = ApprovalConsumeRequest(run_id="r1", interrupt_id="i1", decision="reject")
    for name, store in backends.items():
        assert store.try_consume(req).status is ConsumeStatus.CONSUMED, name
        assert store.try_consume(req).status is ConsumeStatus.DUPLICATE, name
        assert store.try_consume(other).status is ConsumeStatus.CONFLICT, name
