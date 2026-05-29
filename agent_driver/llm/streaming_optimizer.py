"""Phase 13 H28 — stream coalescer for ``LlmStreamEvent`` sources.

Rationale: many LLM providers (OpenAI-compatible, Anthropic, vLLM) emit
one ``delta_text`` event per token or per small token group. At ~50
tokens/second that is one async iteration every ~20ms. For chat UIs
that re-render on every chunk, this causes visible flicker (operator
observation in `my-findings-about-last.md`: "Runs page обновляется
каждые 5 секунд" — the underlying coarse cadence is exactly what
prompted this slice).

This module provides an opt-in async generator wrapper that:

* batches consecutive ``delta_text`` events into ~``window_ms``-wide
  windows (default 80ms);
* flushes the pending buffer on any **non-delta** event so tool-call
  starts / stops / message-stop reach consumers without delay;
* flushes on **idle** — if no new event arrives within ``idle_ms``
  (default 200ms) the pending buffer is emitted as a single chunk so
  partial output is never held indefinitely;
* leaves the ``delta_reasoning`` channel unaltered (reasoning is a
  separate live-thinking channel — its cadence is operator-visible
  signal, not noise, so coalescing would hurt UX).

The function is a pure transform over ``AsyncIterator[LlmStreamEvent]``
— callers wrap their provider stream explicitly when they want the
optimization. No provider config, no SDK contract change. Composes
cleanly with arbitrary downstream consumers (websocket, SSE, REPL).

Example::

    async def handler(...) -> AsyncIterator[LlmStreamEvent]:
        raw = provider.stream(request)
        async for event in coalesce_stream(raw, window_ms=80, idle_ms=200):
            yield event
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from agent_driver.llm.contracts import LlmStreamEvent

_DELTA_EVENT_NAME = "delta"


def _is_coalescable_delta(event: LlmStreamEvent) -> bool:
    """A delta is coalescable iff it carries ONLY plain text.

    Events with reasoning content, a finish reason, or usage data pass
    through unchanged so consumers see them at their natural cadence.
    """
    if event.event != _DELTA_EVENT_NAME:
        return False
    if event.finish_reason is not None:
        return False
    if event.usage is not None:
        return False
    if event.delta_reasoning:
        return False
    if not event.delta_text:
        # Empty deltas are heartbeats from some providers; passing them
        # through has no perceptible cost and avoids surprising callers
        # who watch for keep-alive signals.
        return False
    return True


def _flush_buffer(buffer: str, last_metadata: dict[str, Any] | None) -> LlmStreamEvent:
    """Emit the accumulated buffer as a single coalesced delta."""
    return LlmStreamEvent(
        event=_DELTA_EVENT_NAME,
        delta_text=buffer,
        finish_reason=None,
        metadata=last_metadata or {},
    )


async def coalesce_stream(
    source: AsyncIterator[LlmStreamEvent],
    *,
    window_ms: float = 80.0,
    idle_ms: float = 200.0,
) -> AsyncIterator[LlmStreamEvent]:
    """Coalesce rapid plain-text deltas while preserving event semantics.

    Parameters:
        source: an async iterator of ``LlmStreamEvent``s, typically from
            a provider's ``stream()`` method.
        window_ms: maximum age (ms) of the accumulation buffer before a
            forced flush. Smaller = lower latency, more events; larger
            = fewer events, more buffering. 80ms is roughly the
            perceptual flicker threshold for typing animations.
        idle_ms: if no event arrives within this many milliseconds AND
            the buffer is non-empty, flush. Catches mid-stream pauses
            without making the consumer wait for the next token.

    Yields:
        ``LlmStreamEvent`` instances:
          * coalesced ``delta`` events with concatenated ``delta_text``;
          * any non-coalescable event (tool-use, done, reasoning,
            finish-reason carrier, etc.) passes through verbatim,
            preceded by a buffer-flush when applicable so order is
            preserved.

    The function consumes ``source`` exactly once and produces a
    bounded number of additional ``LlmStreamEvent`` objects (at most
    one per ``window_ms``-window plus passthroughs).
    """
    if window_ms < 0:
        raise ValueError("window_ms must be >= 0")
    if idle_ms <= 0:
        raise ValueError("idle_ms must be > 0")

    buffer = ""
    buffer_start: float | None = None
    last_metadata: dict[str, Any] | None = None

    # Bridge the async iterator into a queue so we can race it against
    # an idle timeout. Background task feeds the queue with None
    # sentinel on exhaustion or with the raised exception.
    queue: asyncio.Queue[LlmStreamEvent | Exception | None] = asyncio.Queue()

    async def _producer() -> None:
        try:
            async for event in source:
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 — preserve any error for caller
            await queue.put(exc)
            return
        await queue.put(None)

    producer_task = asyncio.create_task(_producer())

    try:
        while True:
            # Compute timeout: when a buffer exists, race window expiry
            # against idle expiry (whichever fires first). When buffer is
            # empty, only idle is meaningful (no-op on timeout but lets
            # us periodically yield control).
            if buffer_start is None:
                timeout_s = idle_ms / 1000.0
            else:
                elapsed_ms = (time.monotonic() - buffer_start) * 1000.0
                window_remaining_ms = max(0.0, window_ms - elapsed_ms)
                timeout_s = min(window_remaining_ms, idle_ms) / 1000.0
            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout_s)
            except asyncio.TimeoutError:
                # Either window expired (buffer aged past window_ms) or
                # idle expired (no new event for idle_ms). In both cases,
                # flushing the pending buffer is the correct action; with
                # an empty buffer the loop simply continues to wait.
                if buffer:
                    yield _flush_buffer(buffer, last_metadata)
                    buffer = ""
                    buffer_start = None
                continue

            if item is None:
                # Source exhausted; drain buffer and stop.
                if buffer:
                    yield _flush_buffer(buffer, last_metadata)
                return
            if isinstance(item, Exception):
                # Propagate producer error after draining buffer so the
                # consumer sees output emitted before the failure.
                if buffer:
                    yield _flush_buffer(buffer, last_metadata)
                raise item

            event = item

            if _is_coalescable_delta(event):
                if buffer_start is None:
                    buffer_start = time.monotonic()
                buffer += event.delta_text
                last_metadata = dict(event.metadata) if event.metadata else None
                # Window flush: if buffer age >= window_ms, emit now.
                if (time.monotonic() - buffer_start) * 1000.0 >= window_ms:
                    yield _flush_buffer(buffer, last_metadata)
                    buffer = ""
                    buffer_start = None
            else:
                # Non-coalescable event — flush pending buffer first to
                # preserve ordering, then pass the event through verbatim.
                if buffer:
                    yield _flush_buffer(buffer, last_metadata)
                    buffer = ""
                    buffer_start = None
                yield event
    finally:
        # Producer might still be running if the consumer stopped early
        # (e.g. exception in downstream). Cancel to avoid an orphan task.
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


__all__ = ["coalesce_stream"]
