"""Cascading abort handles for runs and their subagents.

Why this exists
---------------

agent-driver had a single cancellation seam — ``RunnerConfig.cancellation_probe``,
a sync callable polled at step boundaries. That works for a "the outside
world wants to stop the run" signal, but it has two structural gaps that
make multi-agent workflows fragile:

1. It is **callable-shaped**, so the caller has to wire a global flag
   (or closure) and remember to flip it. There is no object identity
   shared between caller and runtime; you cannot say "give me a child
   handle that also stops when I do".

2. It is **single-run**. The Stage 1 / B0.1 work introduces
   ``run_subagent`` — children spawned inside a parent run. Without a
   hierarchy, an operator clicking *Stop* in the UI has no way to cancel
   the tree; the parent gets the signal, runs to completion, then the
   child finishes whatever it was doing.

The pattern lifted from OpenClaude (``src/utils/abortController.ts``) is
a WeakRef-based parent → children abort hierarchy: parent ``.abort()``
flips its own state and every weakly-held child's state. The WeakRef
avoids refcycles when a finished subagent is garbage-collected.

API shape
---------

``RunAbortHandle`` is an opaque, thread-safe primitive that the caller
constructs and passes into ``Agent.run(...)`` / ``Runner.run(...)`` via
the ``abort_handle=`` runtime kwarg. It is **deliberately not** a field
on ``AgentRunInput`` — that contract is JSON-serialisable for transport
and checkpoint storage, and the handle holds a live ``threading.Lock``
plus a ``WeakSet``. Keeping it out of the contract keeps the contract
boundary clean.

Polling vs. waiting
-------------------

Runs check ``handle.is_aborted`` at step boundaries (LLM call, tool
result, subagent spawn). That bounds abort latency to roughly the
slowest in-flight step. For callers that want to ``await`` an abort
event (e.g. a watcher coroutine), ``wait_aborted()`` does a 50 ms poll
loop — good enough for the rare case it is used, and it avoids the
asyncio.Event cross-loop-thread pitfalls.

Why ``threading.Lock`` and not ``asyncio.Lock``
-----------------------------------------------

``.abort()`` is called from HTTP handler threads, websocket pumps,
signal handlers — not necessarily the event loop thread. A
``threading.Lock`` is the only primitive that does the right thing
across those contexts. The lock window is microseconds; contention is
not measurable in practice.
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from typing import Iterable


class RunAbortHandle:
    """Cascading, thread-safe abort signal for a run and its subagents.

    Construct one per top-level run; pass it into ``Agent.run`` /
    ``Runner.run`` as the ``abort_handle=`` runtime parameter. The
    runtime polls ``is_aborted`` at step boundaries and terminates the
    run with ``RunStatus.CANCELLED`` /
    ``TerminalReason.CANCELLED_BY_USER`` when set.

    When a subagent is spawned (see ``run_subagent`` in the SDK), it
    receives a child handle via ``parent.child()`` — calling
    ``parent.abort()`` then cascades to every child held weakly by the
    parent. Children can also be aborted individually without affecting
    the parent.
    """

    # ``__weakref__`` is required so instances can be stored in the
    # parent's ``WeakSet``. Without it, defining ``__slots__`` would
    # prevent any weak reference from being taken on the instance.
    __slots__ = ("_aborted", "_reason", "_children", "_lock", "__weakref__")

    def __init__(self) -> None:
        self._aborted: bool = False
        self._reason: str | None = None
        self._children: "weakref.WeakSet[RunAbortHandle]" = weakref.WeakSet()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ state

    @property
    def is_aborted(self) -> bool:
        """True once ``.abort()`` has been called (on self or an ancestor)."""
        return self._aborted

    @property
    def reason(self) -> str | None:
        """First non-empty reason supplied to ``.abort()``. ``None`` if not yet aborted."""
        return self._reason

    # ------------------------------------------------------------------ mutate

    def abort(self, reason: str = "user_cancel") -> None:
        """Flip self to aborted; cascade to every weakly-held child.

        Idempotent — calling twice keeps the original reason. Safe to
        call from any thread. The cascade releases the lock before
        descending into children so deep trees can't deadlock if a
        child's ``.abort()`` somehow re-enters our lock.
        """
        with self._lock:
            if self._aborted:
                return
            self._aborted = True
            self._reason = reason
            children = list(self._children)
        for child in children:
            child.abort(reason)

    def child(self) -> "RunAbortHandle":
        """Return a new handle linked to self via a weak reference.

        If self is already aborted, the child is born aborted with the
        same reason — saves the caller a check before subagent spawn.
        """
        new_child = RunAbortHandle()
        with self._lock:
            self._children.add(new_child)
            already_aborted = self._aborted
            inherited_reason = self._reason
        if already_aborted:
            new_child.abort(inherited_reason or "parent_aborted")
        return new_child

    # ------------------------------------------------------------------ async

    async def wait_aborted(self, *, poll_interval_s: float = 0.05) -> None:
        """Block the current coroutine until aborted.

        Polling-based rather than ``asyncio.Event`` because ``.abort()``
        may be called from a non-loop thread, and ``Event.set()`` from
        the wrong thread is not safe. The 50 ms default keeps the busy
        loop's CPU cost negligible (~0.02 % of one core).

        Callers that need lower abort latency than 50 ms should lower
        ``poll_interval_s`` themselves — there's no global default
        because the right interval depends on what the caller is doing
        with the signal.
        """
        while not self._aborted:
            await asyncio.sleep(poll_interval_s)

    # ------------------------------------------------------------------ debug

    def _live_children(self) -> Iterable["RunAbortHandle"]:
        """Snapshot of currently-live children. Test-only."""
        with self._lock:
            return list(self._children)


__all__ = ["RunAbortHandle"]
