"""Lifecycle hook adapting a long-term :class:`MemoryProvider` to a run.

Keeps the memory package free of runtime imports: the memory library stays a
pure store/provider, and this runtime-side adapter plugs it into the run
lifecycle. Recall happens once at run start (stored via ``MemoryRuntimeState``
so it survives checkpoint/resume and is injected into the system prompt); the
finished turn is persisted exactly once per run, off the critical path: sync
is *scheduled* at ``on_run_completed`` (after any goal-gate revision loop has
settled, so the stored answer is the one the user received) and awaited only
at ``shutdown()``. A fact-extraction provider makes an LLM call in
``sync_turn`` — awaiting that inline was measured to hold ``run_completed``
hostage for tens of seconds while the UI showed a stale progress label.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent_driver.memory.provider import (
    MemoryProvider,
    MemoryTurn,
    RecallQuery,
    render_recall_block,
)
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook
from agent_driver.runtime.metadata_state import get_memory_runtime_state

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext

logger = logging.getLogger(__name__)


def _memory_overrides(context: "RunContext") -> dict:
    """Return the host-supplied ``app_metadata["memory"]`` override mapping.

    Hosts whose ``run_input.input`` is a composed prompt (RAG context, ledgers,
    instructions) can pass the clean user utterance here so long-term memory
    stores/queries the actual question instead of the full prompt envelope.
    Supported keys: ``user_text`` (what to persist as the user side of the
    turn), ``recall_query`` (what to match recall against).
    """
    overrides = (context.run_input.app_metadata or {}).get("memory")
    return overrides if isinstance(overrides, dict) else {}


def _override_text(overrides: dict, key: str) -> str | None:
    value = overrides.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class MemoryLifecycleHook(BaseRunLifecycleHook):
    """Bridge a :class:`MemoryProvider` into run-start recall and finalize sync."""

    name = "long_term_memory"

    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider
        self._post_setup_done = False
        self._pending_syncs: set[asyncio.Task] = set()

    async def _ensure_post_setup(self) -> None:
        """Run the provider's one-time ``post_setup`` lazily and idempotently."""
        if self._post_setup_done:
            return
        self._post_setup_done = True
        await self._provider.post_setup()

    async def shutdown(self) -> None:
        """Flush pending turn syncs, then close the backing provider."""
        pending = tuple(self._pending_syncs)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self._provider.shutdown()

    async def on_run_start(self, context: "RunContext") -> None:
        await self._ensure_post_setup()
        session_id = context.run_input.thread_id
        memory_state = get_memory_runtime_state(context)
        if not session_id or memory_state.has_recalled():
            return
        # Read-your-writes: a still-running background sync from the previous
        # run of this agent must land before this run's recall query.
        pending = tuple(self._pending_syncs)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        overrides = _memory_overrides(context)
        query_text = (
            _override_text(overrides, "recall_query")
            or (context.run_input.input or "").strip()
            or None
        )
        result = await self._provider.prefetch(
            RecallQuery(session_id=session_id, query=query_text)
        )
        # Recall budget: providers may expose `recall_max_chars` to bound how much
        # recalled memory enters the system prompt (epic 021 phase C).
        max_chars = getattr(self._provider, "recall_max_chars", None)
        block = render_recall_block(
            result, max_chars=int(max_chars) if max_chars else 2000
        )
        if block:
            memory_state.set_recalled_block(block)
        # Raw-free observability (epic 021 phase D): counts only, никакого текста.
        context.metadata["memory_recall_count"] = len(result.records)

    async def on_run_completed(self, context: "RunContext", *, answer: str) -> None:
        session_id = context.run_input.thread_id
        memory_state = get_memory_runtime_state(context)
        if not session_id or memory_state.turn_synced():
            return
        memory_state.mark_turn_synced()
        turn = MemoryTurn(
            session_id=session_id,
            run_id=context.run_id,
            user_text=(
                _override_text(_memory_overrides(context), "user_text")
                or (context.run_input.input or "").strip()
                or None
            ),
            assistant_text=answer or None,
        )
        if not getattr(self._provider, "defer_sync", False):
            # Cheap store-backed sync keeps its historical contract: the run is
            # complete only once the turn is durably recorded.
            await self._provider.sync_turn(turn)
            return
        # Deferred: sync_turn makes an LLM call (fact extraction) and must not
        # delay the run's completion. shutdown()/next recall await stragglers.
        task = asyncio.create_task(self._provider.sync_turn(turn))
        self._pending_syncs.add(task)

        def _reap(done: asyncio.Task) -> None:
            self._pending_syncs.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.warning(
                    "memory turn sync failed in background", exc_info=done.exception()
                )

        task.add_done_callback(_reap)


__all__ = ["MemoryLifecycleHook"]
