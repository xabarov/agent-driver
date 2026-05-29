"""Steering control-plane contract and queue tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    CommandQueueStatus,
    ControlKind,
    ControlPriority,
    ControlRequest,
)
from agent_driver.runtime.control import (
    InMemoryCommandQueueStore,
    SqliteCommandQueueStore,
)


def _request(
    kind: ControlKind,
    *,
    priority: ControlPriority = ControlPriority.NEXT,
    dedupe_key: str | None = None,
) -> ControlRequest:
    return ControlRequest(
        kind=kind,
        run_id="run_control",
        priority=priority,
        payload={"message": kind.value},
        dedupe_key=dedupe_key,
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    """Run queue store behavior tests against memory and SQLite stores."""
    if request.param == "sqlite":
        return SqliteCommandQueueStore(path=str(tmp_path / "control.db"))
    return InMemoryCommandQueueStore()


def test_control_request_requires_routing_identifier() -> None:
    """Controls must be routed to a run/thread/agent."""
    with pytest.raises(ValueError):
        ControlRequest(kind=ControlKind.INTERRUPT)


def test_command_queue_priority_order_and_fifo_within_priority(store) -> None:
    """Queue ordering should be now > next > later, FIFO within each priority."""
    later = store.enqueue(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, priority=ControlPriority.LATER)
    )
    next_one = store.enqueue(
        _request(ControlKind.SET_MODEL, priority=ControlPriority.NEXT)
    )
    now_one = store.enqueue(
        _request(ControlKind.INTERRUPT, priority=ControlPriority.NOW)
    )
    next_two = store.enqueue(
        _request(ControlKind.SET_TOOL_POLICY, priority=ControlPriority.NEXT)
    )

    ordered = store.list_pending(run_id="run_control")

    assert [item.queue_id for item in ordered] == [
        now_one.queue_id,
        next_one.queue_id,
        next_two.queue_id,
        later.queue_id,
    ]
    assert store.dequeue_next(run_id="run_control") == now_one


def test_command_queue_cancel_and_mark_applied_remove_from_pending(store) -> None:
    """Cancelled/applied items should no longer appear in pending results."""
    cancelled = store.enqueue(_request(ControlKind.ENQUEUE_USER_MESSAGE))
    applied = store.enqueue(_request(ControlKind.SET_MODEL))

    assert store.cancel(cancelled.queue_id).status == CommandQueueStatus.CANCELLED
    assert store.mark_applied(applied.queue_id).status == CommandQueueStatus.APPLIED

    assert store.list_pending(run_id="run_control") == []


def test_command_queue_dedupe_key_returns_existing_pending_item(store) -> None:
    """Dedupe key should avoid duplicate pending queue items."""
    first = store.enqueue(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, dedupe_key="same-message")
    )
    second = store.enqueue(
        _request(ControlKind.ENQUEUE_USER_MESSAGE, dedupe_key="same-message")
    )

    assert second.queue_id == first.queue_id
    assert len(store.list_pending(run_id="run_control")) == 1


def test_command_queue_route_filters(store) -> None:
    """Pending list should filter by run/thread/agent route."""
    run_item = store.enqueue(_request(ControlKind.INTERRUPT))
    thread_item = store.enqueue(
        ControlRequest(
            kind=ControlKind.ENQUEUE_USER_MESSAGE,
            thread_id="thread_control",
            priority=ControlPriority.NOW,
        )
    )

    assert store.list_pending(run_id="run_control") == [run_item]
    assert store.list_pending(thread_id="thread_control") == [thread_item]


def test_sqlite_command_queue_persists_rows(tmp_path) -> None:
    """SQLite queue should survive store re-instantiation."""
    path = tmp_path / "control.db"
    first_store = SqliteCommandQueueStore(path=str(path))
    item = first_store.enqueue(_request(ControlKind.SET_MODEL))

    second_store = SqliteCommandQueueStore(path=str(path))

    loaded = second_store.get(item.queue_id)
    assert loaded is not None
    assert loaded.queue_id == item.queue_id
    assert second_store.dequeue_next(run_id="run_control").queue_id == item.queue_id
