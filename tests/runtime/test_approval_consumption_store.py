"""U3 B/C (epic 051) — atomic, durable approval-consumption ledger."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_driver.runtime.control.approval_store import (
    ApprovalConsumeRequest,
    ConsumeStatus,
    InMemoryApprovalConsumptionStore,
    SqliteApprovalConsumptionStore,
)


def _store(kind: str, tmp_path):
    if kind == "memory":
        return InMemoryApprovalConsumptionStore()
    return SqliteApprovalConsumptionStore(path=str(tmp_path / "approvals.db"))


def _req(**kw):
    base = {"run_id": "r1", "interrupt_id": "int1", "decision": "approve"}
    base.update(kw)
    return ApprovalConsumeRequest(**base)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_first_consume_wins_duplicate_loses(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    first = store.try_consume(_req())
    assert first.status is ConsumeStatus.CONSUMED and first.is_first
    second = store.try_consume(_req())
    assert second.status is ConsumeStatus.DUPLICATE
    assert not second.is_first


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_conflicting_decision_is_conflict(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    assert store.try_consume(_req(decision="approve")).status is ConsumeStatus.CONSUMED
    out = store.try_consume(_req(decision="reject"))
    assert out.status is ConsumeStatus.CONFLICT
    assert out.prior_decision == "approve"


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_same_idempotency_key_different_interrupt_is_duplicate(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    assert (
        store.try_consume(_req(interrupt_id="intA", idempotency_key="k1")).status
        is ConsumeStatus.CONSUMED
    )
    out = store.try_consume(_req(interrupt_id="intB", idempotency_key="k1"))
    assert out.status is ConsumeStatus.DUPLICATE


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_record_result_is_returned_on_duplicate(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    store.try_consume(_req())
    store.record_result(run_id="r1", interrupt_id="int1", result_ref="out-42")
    dup = store.try_consume(_req())
    assert dup.status is ConsumeStatus.DUPLICATE
    assert dup.prior_result_ref == "out-42"
    got = store.get(run_id="r1", interrupt_id="int1")
    assert got is not None and got.prior_result_ref == "out-42"


def test_sqlite_survives_new_instance(tmp_path) -> None:
    path = str(tmp_path / "approvals.db")
    SqliteApprovalConsumptionStore(path=path).try_consume(_req())
    # A fresh store (as after a restart) still sees the consumption.
    reopened = SqliteApprovalConsumptionStore(path=path)
    assert reopened.try_consume(_req()).status is ConsumeStatus.DUPLICATE


def test_sqlite_cas_is_atomic_under_concurrency(tmp_path) -> None:
    path = str(tmp_path / "approvals.db")
    # Ensure schema exists before the racing writers open their own connections.
    SqliteApprovalConsumptionStore(path=path)
    n = 16

    def _attempt(_i: int) -> ConsumeStatus:
        # Each "client" is an independent store instance (own connection),
        # standing in for separate processes racing the same approval.
        store = SqliteApprovalConsumptionStore(path=path)
        return store.try_consume(_req()).status

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(_attempt, range(n)))

    consumed = [s for s in results if s is ConsumeStatus.CONSUMED]
    assert len(consumed) == 1, results  # exactly one winner
    assert all(s is ConsumeStatus.DUPLICATE for s in results if s is not ConsumeStatus.CONSUMED)
