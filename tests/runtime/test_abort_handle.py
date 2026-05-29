"""Tests for :class:`RunAbortHandle` — the cascading abort primitive."""

from __future__ import annotations

import asyncio
import gc
import threading
import time

import pytest

from agent_driver.runtime.abort import RunAbortHandle


# ---------------------------------------------------------------------------
# Solo handle
# ---------------------------------------------------------------------------


def test_fresh_handle_is_not_aborted() -> None:
    """Default constructed → ``is_aborted`` is False, ``reason`` is None."""
    handle = RunAbortHandle()
    assert handle.is_aborted is False
    assert handle.reason is None


def test_abort_flips_state_and_records_reason() -> None:
    """Single ``.abort('foo')`` sets the flag + reason."""
    handle = RunAbortHandle()
    handle.abort("user_cancel")
    assert handle.is_aborted is True
    assert handle.reason == "user_cancel"


def test_abort_is_idempotent_first_reason_wins() -> None:
    """Calling ``.abort()`` twice keeps the original reason — later
    callers can't silently rewrite history."""
    handle = RunAbortHandle()
    handle.abort("first")
    handle.abort("second")
    assert handle.reason == "first"


def test_default_reason_is_user_cancel() -> None:
    """No-argument abort uses the conventional ``user_cancel`` reason."""
    handle = RunAbortHandle()
    handle.abort()
    assert handle.reason == "user_cancel"


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def test_child_starts_clean_when_parent_clean() -> None:
    parent = RunAbortHandle()
    child = parent.child()
    assert child.is_aborted is False
    assert child.reason is None


def test_parent_abort_cascades_to_child() -> None:
    """Aborting the parent flips every weakly-held child."""
    parent = RunAbortHandle()
    child = parent.child()
    parent.abort("user_cancel")
    assert child.is_aborted is True
    assert child.reason == "user_cancel"


def test_parent_abort_cascades_through_multiple_children() -> None:
    """Cascade reaches every live child, regardless of order."""
    parent = RunAbortHandle()
    children = [parent.child() for _ in range(5)]
    parent.abort("cleanup")
    for child in children:
        assert child.is_aborted is True


def test_child_abort_does_not_propagate_to_parent() -> None:
    """A child can abort independently without taking down the parent —
    e.g. a single subagent fails but the run continues."""
    parent = RunAbortHandle()
    child = parent.child()
    child.abort("child_only")
    assert child.is_aborted is True
    assert parent.is_aborted is False


def test_child_abort_does_not_propagate_to_siblings() -> None:
    """Siblings are independent. Only the parent → children direction
    cascades."""
    parent = RunAbortHandle()
    sibling_a = parent.child()
    sibling_b = parent.child()
    sibling_a.abort("a_only")
    assert sibling_a.is_aborted is True
    assert sibling_b.is_aborted is False


def test_grandchild_inherits_via_chain() -> None:
    """Cascade walks the whole tree — parent → child → grandchild."""
    parent = RunAbortHandle()
    child = parent.child()
    grandchild = child.child()
    parent.abort("top")
    assert grandchild.is_aborted is True


def test_child_born_already_aborted_when_parent_aborted_first() -> None:
    """If parent is aborted before the child is created, the child
    inherits the aborted state on construction — saves the caller a
    check before subagent spawn."""
    parent = RunAbortHandle()
    parent.abort("first")
    child = parent.child()
    assert child.is_aborted is True
    assert child.reason == "first"


# ---------------------------------------------------------------------------
# Memory hygiene — WeakRef
# ---------------------------------------------------------------------------


def test_parent_does_not_keep_finished_children_alive() -> None:
    """A child that goes out of scope must be garbage-collectible —
    the parent only holds a WeakRef. This is the contract that prevents
    long-running parents from accumulating dead subagent handles."""
    parent = RunAbortHandle()
    # Create a child without keeping a strong reference
    parent.child()
    gc.collect()
    # The WeakSet should now be empty
    assert list(parent._live_children()) == []


def test_parent_keeps_strongly_referenced_child_alive() -> None:
    """Sanity check on the WeakRef test — if the caller holds a strong
    ref, the child is still in the WeakSet."""
    parent = RunAbortHandle()
    child = parent.child()
    gc.collect()
    live = list(parent._live_children())
    assert len(live) == 1
    assert live[0] is child


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_abort_is_thread_safe_across_concurrent_callers() -> None:
    """100 threads simultaneously call ``.abort()`` with distinct
    reasons. Exactly one wins; the rest no-op without crashing.

    The lock window is tiny so contention rarely surfaces bugs in
    practice — this just smoke-tests the API surface."""
    handle = RunAbortHandle()
    barrier = threading.Barrier(101)  # 100 callers + main

    def caller(idx: int) -> None:
        barrier.wait()
        handle.abort(f"reason_{idx}")

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    barrier.wait()
    for t in threads:
        t.join()
    assert handle.is_aborted is True
    # Any of the 100 reasons could have won — just assert the format.
    assert handle.reason is not None and handle.reason.startswith("reason_")


def test_abort_from_non_loop_thread_visible_to_loop() -> None:
    """``.abort()`` called from a worker thread is visible to the
    asyncio loop without needing ``call_soon_threadsafe`` — the flag
    is just a bool, not an asyncio.Event."""
    handle = RunAbortHandle()

    async def runner() -> bool:
        # Schedule an abort from a worker thread after 50 ms
        threading.Timer(0.05, lambda: handle.abort("worker")).start()
        # Wait up to 1 s for the flag to flip
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if handle.is_aborted:
                return True
            await asyncio.sleep(0.01)
        return False

    assert asyncio.run(runner()) is True
    assert handle.reason == "worker"


# ---------------------------------------------------------------------------
# wait_aborted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_aborted_returns_when_flag_flips() -> None:
    """``wait_aborted`` resolves shortly after ``.abort()`` is called."""
    handle = RunAbortHandle()

    async def aborter() -> None:
        await asyncio.sleep(0.05)
        handle.abort("delayed")

    start = asyncio.get_event_loop().time()
    await asyncio.gather(handle.wait_aborted(poll_interval_s=0.01), aborter())
    elapsed = asyncio.get_event_loop().time() - start
    # Should return within ~poll_interval after the abort; give 200 ms margin.
    assert elapsed < 0.25
    assert handle.is_aborted is True


@pytest.mark.asyncio
async def test_wait_aborted_returns_immediately_when_already_aborted() -> None:
    """If the handle is already aborted when ``wait_aborted`` is
    called, the coroutine resolves on the next poll tick — no need
    to actually wait."""
    handle = RunAbortHandle()
    handle.abort("eager")
    start = asyncio.get_event_loop().time()
    await handle.wait_aborted(poll_interval_s=0.01)
    elapsed = asyncio.get_event_loop().time() - start
    # Should return on the first tick.
    assert elapsed < 0.05
