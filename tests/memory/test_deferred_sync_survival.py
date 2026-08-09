"""Deferred turn-sync survives the per-request agent lifecycle.

A deferred sync (fact extraction) is scheduled at run completion and may outlive
the agent that scheduled it. In a per-request server the hook holds the only
strong reference (via ``_pending_syncs``) and is discarded the instant the run
returns; asyncio keeps only weak refs to tasks, so the task was GC'd mid-flight
before persisting anything. A process-global anchor keeps the task alive until it
finishes, and run-start recall drains same-session writes across per-request
agents (read-your-writes). Regression test for the bug found in live excel-ai E2E.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_driver.memory import InMemoryMemoryStore
from agent_driver.memory.provider import (
    MemoryKind,
    MemoryProvider,
    MemoryRecord,
    RecallResult,
)
from agent_driver.runtime.single_agent.lifecycle.memory_hook import (
    _LIVE_SESSION_SYNCS,
    MemoryLifecycleHook,
    _drain_session_syncs,
)
from agent_driver.runtime.metadata_state import get_memory_runtime_state


class _SlowDeferredProvider(MemoryProvider):
    """Deferred provider whose sync_turn takes a beat (like an extraction call)."""

    defer_sync = True

    def __init__(self, store) -> None:
        self._store = store
        self.recall_max_chars = 2000

    @property
    def store(self):
        return self._store

    async def prefetch(self, query):
        records = self._store.list_for_session(query.session_id)[: query.limit]
        return RecallResult(session_id=query.session_id, records=records)

    async def sync_turn(self, turn) -> None:
        await asyncio.sleep(0.05)  # simulate the extraction latency
        self._store.append(
            MemoryRecord(
                session_id=turn.session_id,
                text=turn.assistant_text or "",
                kind=MemoryKind.FACT,
            )
        )


def _ctx(thread_id: str, run_id: str, *, user_input: str = "hi") -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        metadata={},
        run_input=SimpleNamespace(
            thread_id=thread_id, input=user_input, app_metadata={}
        ),
    )


@pytest.mark.asyncio
async def test_deferred_sync_is_anchored_and_completes() -> None:
    store = InMemoryMemoryStore()
    hook = MemoryLifecycleHook(_SlowDeferredProvider(store))
    await hook.on_run_completed(_ctx("s1", "r1"), answer="deploy target is eu-west-3")

    # Anchored process-globally, so it survives the hook/agent being discarded.
    assert _LIVE_SESSION_SYNCS.get("s1")

    # Drop the hook (per-request agent discarded); the anchor keeps the task alive.
    del hook
    import gc

    gc.collect()

    await _drain_session_syncs("s1")
    assert any("eu-west-3" in r.text for r in store.list_for_session("s1"))
    assert "s1" not in _LIVE_SESSION_SYNCS  # released on completion


@pytest.mark.asyncio
async def test_recall_awaits_prior_per_request_agents_write() -> None:
    """The excel-ai case: a FRESH agent recalls before the previous agent's
    deferred sync has landed — it must still see the write (read-your-writes
    across per-request agents, whose hooks don't share ``_pending_syncs``)."""
    store = InMemoryMemoryStore()

    # Agent 1 finishes a run → schedules a deferred sync, then is discarded.
    hook1 = MemoryLifecycleHook(_SlowDeferredProvider(store))
    await hook1.on_run_completed(_ctx("s1", "r1"), answer="deploy target is eu-west-3")
    del hook1

    # Agent 2 (a new request, empty instance _pending_syncs) recalls for the same
    # session immediately — before agent 1's task has finished.
    hook2 = MemoryLifecycleHook(_SlowDeferredProvider(store))
    ctx2 = _ctx("s1", "r2", user_input="where do we deploy?")
    await hook2.on_run_start(ctx2)

    block = get_memory_runtime_state(ctx2).recalled_block()
    assert block is not None and "eu-west-3" in block


@pytest.mark.asyncio
async def test_anchor_is_per_session() -> None:
    store = InMemoryMemoryStore()
    hook = MemoryLifecycleHook(_SlowDeferredProvider(store))
    await hook.on_run_completed(_ctx("sA", "r1"), answer="fact A")
    await hook.on_run_completed(_ctx("sB", "r2"), answer="fact B")
    assert set(_LIVE_SESSION_SYNCS) == {"sA", "sB"}
    await _drain_session_syncs("sA")
    await _drain_session_syncs("sB")
    assert [r.text for r in store.list_for_session("sA")] == ["fact A"]
    assert [r.text for r in store.list_for_session("sB")] == ["fact B"]
