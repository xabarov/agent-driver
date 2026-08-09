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

# Per-session consolidation locks (epic 031), process-global so two overlapping
# runs of the same user in the one jobworker never rewrite the store at once.
# asyncio.Lock binds to the loop lazily on first await, so a module dict is safe
# for the single-loop worker; a stale lock from a dead test loop is simply unused.
_CONSOLIDATION_LOCKS: dict[str, asyncio.Lock] = {}


def _consolidation_lock(session_id: str) -> asyncio.Lock:
    lock = _CONSOLIDATION_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _CONSOLIDATION_LOCKS[session_id] = lock
    return lock


# Process-global anchor for deferred turn-syncs, keyed by session.
#
# A deferred sync (fact extraction) is scheduled at ``on_run_completed`` and can
# outlive the agent that scheduled it. In a per-request server the hook holds the
# ONLY strong reference to the task (via ``self._pending_syncs``) and is discarded
# the instant the request returns — and asyncio keeps only *weak* references to
# tasks, so the task is garbage-collected mid-flight before its extraction call
# runs and nothing is ever persisted. Anchoring the task here keeps a strong
# reference until it completes, independent of the hook/agent lifecycle, and lets
# a later run in the same session await same-session writes (read-your-writes
# across per-request agents). The done-callback releases the reference.
_LIVE_SESSION_SYNCS: dict[str, set[asyncio.Task]] = {}


def _anchor_sync_task(session_id: str, task: asyncio.Task) -> None:
    """Keep a process-global strong ref to ``task`` until it finishes."""
    bucket = _LIVE_SESSION_SYNCS.setdefault(session_id, set())
    bucket.add(task)

    def _release(done: asyncio.Task) -> None:
        bucket.discard(done)
        if not bucket:
            _LIVE_SESSION_SYNCS.pop(session_id, None)

    task.add_done_callback(_release)


async def _drain_session_syncs(session_id: str) -> None:
    """Await any in-flight deferred syncs for ``session_id`` (read-your-writes)."""
    pending = tuple(_LIVE_SESSION_SYNCS.get(session_id, ()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


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

    # Bounded shutdown drain (epic 024, hermes-style): flush pending background
    # syncs for at most this long, then report what was abandoned instead of
    # holding process exit hostage to a wedged provider.
    _SHUTDOWN_DRAIN_TIMEOUT = 30.0

    def __init__(
        self,
        provider: MemoryProvider,
        *,
        shutdown_drain_timeout: float | None = None,
        consolidation_every_n_turns: int = 0,
    ) -> None:
        self._provider = provider
        self._post_setup_done = False
        self._pending_syncs: set[asyncio.Task] = set()
        self._shutdown_drain_timeout = (
            shutdown_drain_timeout
            if shutdown_drain_timeout is not None
            else self._SHUTDOWN_DRAIN_TIMEOUT
        )
        # Epic 031: 0 disables background consolidation entirely.
        self._consolidation_every_n_turns = max(0, int(consolidation_every_n_turns))

    async def _ensure_post_setup(self) -> None:
        """Run the provider's one-time ``post_setup`` lazily and idempotently."""
        if self._post_setup_done:
            return
        self._post_setup_done = True
        await self._provider.post_setup()

    async def shutdown(self) -> None:
        """Drain pending turn syncs (bounded), report abandons, close provider."""
        pending = tuple(self._pending_syncs)
        if pending:
            _done, not_done = await asyncio.wait(
                pending, timeout=self._shutdown_drain_timeout
            )
            if not_done:
                # Honest shutdown report instead of a silent hang or silent loss
                # (hermes memory_manager reports abandoned_writes the same way).
                logger.warning(
                    "memory shutdown: %d background turn sync(s) abandoned "
                    "after %.0fs drain",
                    len(not_done),
                    self._shutdown_drain_timeout,
                )
        await self._provider.shutdown()

    async def on_run_start(self, context: "RunContext") -> None:
        await self._ensure_post_setup()
        session_id = context.run_input.thread_id
        memory_state = get_memory_runtime_state(context)
        if not session_id or memory_state.has_recalled():
            return
        # Read-your-writes: a still-running background sync must land before this
        # run's recall query — whether it was scheduled by THIS agent or by a
        # prior per-request agent for the same session whose hook is already gone
        # (the global anchor keeps such a task drainable here).
        await _drain_session_syncs(session_id)
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
        # Write-context gate (epic 027, hermes agent_context): background,
        # benchmark or subagent runs opt out of polluting the user's memory
        # with app_metadata["memory"]["sync"] = False (recall unaffected).
        if _memory_overrides(context).get("sync") is False:
            return
        memory_state.mark_turn_synced()
        # Epic M1 (openclaude mutual exclusion): the model curated memory itself
        # this turn via the `remember` tool. Flush those explicit writes and SKIP
        # the automatic turn-sync/extraction — no double-write, the extraction
        # LLM call is saved, and the model's chosen facts are the higher-signal
        # record. The extractor stays the safety net for turns the model didn't
        # curate. Explicit writes are synchronous, so they are durable at
        # completion and visible to the next run's recall with no drain.
        explicit = memory_state.pending_writes()
        if explicit:
            # Route through the provider so a re-scoping provider (e.g. a
            # workbook-scoped wrapper) persists explicit facts under its own
            # scope. A storeless provider returns None → fall through to sync_turn.
            written = await self._provider.record_explicit_writes(
                session_id, explicit, run_id=context.run_id
            )
            if written is not None:
                context.metadata["memory_explicit_synced_count"] = written
                return
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
        # Consolidation (epic 031) is chained AFTER the sync in the same task so
        # it never races the append it depends on, and is drained by the same
        # bounded shutdown path.
        task = asyncio.create_task(self._sync_then_maybe_consolidate(turn, context))
        self._pending_syncs.add(task)
        # Survival anchor: keep a process-global strong ref so the task completes
        # even if this hook/agent is discarded the moment the run returns (the
        # per-request server case — otherwise the task is GC'd before it persists).
        _anchor_sync_task(session_id, task)

        def _reap(done: asyncio.Task) -> None:
            self._pending_syncs.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.warning(
                    "memory turn sync failed in background", exc_info=done.exception()
                )

        task.add_done_callback(_reap)

    async def _sync_then_maybe_consolidate(
        self, turn: MemoryTurn, context: "RunContext"
    ) -> None:
        """Persist the turn, then fire a consolidation pass if the cadence lands."""
        await self._provider.sync_turn(turn)
        await self._maybe_consolidate(context, turn.session_id)

    def _turn_ordinal(self, context: "RunContext") -> int:
        """Durable turn number for this session, supplied by the host.

        The engine is stateless across turns (a fresh runner per chat turn), so
        the cadence counter cannot live on the hook — the host, which owns the
        durable conversation, passes it via ``app_metadata["memory"]``.
        """
        raw = _memory_overrides(context).get("turn_ordinal")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def _maybe_consolidate(self, context: "RunContext", session_id: str) -> None:
        """Fire a consolidation pass when the cadence gate lands (epic 031).

        Gated cheaply first (interval off / not a cadence turn / provider has no
        consolidate) before taking the per-session lock. If a consolidation is
        already in flight for this session, skip rather than queue — the next
        cadence turn will pick up any residue.
        """
        every_n = self._consolidation_every_n_turns
        if every_n <= 0:
            return
        ordinal = self._turn_ordinal(context)
        if ordinal <= 0 or ordinal % every_n != 0:
            return
        if not hasattr(self._provider, "consolidate"):
            return
        lock = _consolidation_lock(session_id)
        if lock.locked():
            return
        async with lock:
            try:
                result = await self._provider.consolidate(session_id)
            except Exception:  # noqa: BLE001 - consolidation must never crash a run
                logger.warning("memory consolidation failed", exc_info=True)
                return
        if result is None:
            return
        # Raw-free observability (counts only): the host reads this off the run's
        # metadata to surface a governance notice — never in the chat stream.
        context.metadata["memory_consolidation"] = {
            "applied": bool(result.applied),
            "reason": result.reason,
            "before": result.before_count,
            "after": result.after_count,
        }


__all__ = ["MemoryLifecycleHook"]
