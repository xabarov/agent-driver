"""U4 A/D (epic 052) — durable abort lifecycle ledger."""

from __future__ import annotations

import pytest

from agent_driver.runtime.control.abort_store import (
    AbortLifecycleState,
    InMemoryAbortLifecycleStore,
    SqliteAbortLifecycleStore,
)


def _store(kind: str, tmp_path):
    if kind == "memory":
        return InMemoryAbortLifecycleStore()
    return SqliteAbortLifecycleStore(path=str(tmp_path / "abort.db"))


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_request_then_observed_then_cancelled(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    r = store.request_abort("run1", reason="user_stop", actor="operator")
    assert r.state is AbortLifecycleState.REQUESTED and r.observed is False
    o = store.mark_observed("run1")
    assert o.state is AbortLifecycleState.OBSERVED and o.observed is True
    assert o.reason == "user_stop" and o.actor == "operator"  # preserved
    c = store.resolve("run1", cancelled=True)
    assert c is not None and c.state is AbortLifecycleState.CANCELLED
    assert c.observed is True and c.is_terminal


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_completed_before_cancel(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    store.request_abort("run2", reason="late")
    out = store.resolve("run2", cancelled=False)
    assert out is not None
    assert out.state is AbortLifecycleState.COMPLETED_BEFORE_CANCEL
    assert out.observed is False  # never observed as a cancellation


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_resolve_without_request_is_noop(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    assert store.resolve("nope", cancelled=False) is None
    assert store.get("nope") is None


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_mark_observed_creates_record_when_absent(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    o = store.mark_observed("run3", reason="handle_abort")
    assert o.state is AbortLifecycleState.OBSERVED and o.observed is True


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_terminal_is_not_moved_backwards(kind, tmp_path) -> None:
    store = _store(kind, tmp_path)
    store.mark_observed("run4")
    store.resolve("run4", cancelled=True)
    # A late mark_observed / re-resolve must not un-terminalise the record.
    assert store.mark_observed("run4").state is AbortLifecycleState.CANCELLED
    assert store.resolve("run4", cancelled=False).state is AbortLifecycleState.CANCELLED


def test_sqlite_survives_new_instance(tmp_path) -> None:
    path = str(tmp_path / "abort.db")
    s = SqliteAbortLifecycleStore(path=path)
    s.mark_observed("run5")
    s.resolve("run5", cancelled=True)
    reopened = SqliteAbortLifecycleStore(path=path)
    got = reopened.get("run5")
    assert got is not None and got.state is AbortLifecycleState.CANCELLED
    assert got.observed is True
