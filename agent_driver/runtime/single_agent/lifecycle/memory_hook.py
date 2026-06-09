"""Lifecycle hook adapting a long-term :class:`MemoryProvider` to a run.

Keeps the memory package free of runtime imports: the memory library stays a
pure store/provider, and this runtime-side adapter plugs it into the run
lifecycle. Recall happens once at run start (stored via ``MemoryRuntimeState``
so it survives checkpoint/resume and is injected into the system prompt); the
finished turn is persisted exactly once at terminal finalize.
"""

from __future__ import annotations

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


class MemoryLifecycleHook(BaseRunLifecycleHook):
    """Bridge a :class:`MemoryProvider` into run-start recall and finalize sync."""

    name = "long_term_memory"

    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider

    async def on_run_start(self, context: "RunContext") -> None:
        session_id = context.run_input.thread_id
        memory_state = get_memory_runtime_state(context)
        if not session_id or memory_state.has_recalled():
            return
        query_text = (context.run_input.input or "").strip() or None
        result = await self._provider.prefetch(
            RecallQuery(session_id=session_id, query=query_text)
        )
        block = render_recall_block(result)
        if block:
            memory_state.set_recalled_block(block)

    async def on_finalize(self, context: "RunContext", *, answer: str) -> None:
        session_id = context.run_input.thread_id
        memory_state = get_memory_runtime_state(context)
        if not session_id or memory_state.turn_synced():
            return
        memory_state.mark_turn_synced()
        await self._provider.sync_turn(
            MemoryTurn(
                session_id=session_id,
                run_id=context.run_id,
                user_text=(context.run_input.input or "").strip() or None,
                assistant_text=answer or None,
            )
        )


__all__ = ["MemoryLifecycleHook"]
