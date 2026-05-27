"""Phase 13 H28 — tests for ``coalesce_stream``.

Pins:
  * window flush: bursts of small deltas within ``window_ms`` are merged
    into one event;
  * idle flush: a pause longer than ``idle_ms`` flushes the pending
    buffer without waiting for the next token;
  * non-delta events flush the buffer first then pass through verbatim
    (tool-use start/stop, done event, finish-reason carrier);
  * delta events with ``delta_reasoning`` pass through unaltered — the
    reasoning channel is operator-visible signal;
  * source exhaustion drains the buffer;
  * producer errors are re-raised AFTER buffer drain so consumers see
    any partial output emitted before the failure;
  * empty inputs (zero events) yield zero events.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from agent_driver.llm.contracts import LlmFinishReason, LlmStreamEvent, UsageSummary
from agent_driver.llm.streaming_optimizer import coalesce_stream


def _delta(text: str, *, delay_ms: float = 0.0) -> tuple[LlmStreamEvent, float]:
    return LlmStreamEvent(event="delta", delta_text=text), delay_ms / 1000.0


async def _drive(
    items: list[tuple[LlmStreamEvent, float]],
) -> AsyncIterator[LlmStreamEvent]:
    """Yield items with the per-item ``delay_s`` slept BEFORE the yield."""
    for event, delay in items:
        if delay > 0:
            await asyncio.sleep(delay)
        yield event


async def _collect(stream: AsyncIterator[LlmStreamEvent]) -> list[LlmStreamEvent]:
    out: list[LlmStreamEvent] = []
    async for event in stream:
        out.append(event)
    return out


# -- window flush -----------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_within_window_merged_into_one_event():
    """Many tiny deltas with no per-event delay all fall in the same window."""
    items = [_delta("a"), _delta("b"), _delta("c"), _delta("d"), _delta("e")]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=200.0, idle_ms=500.0)
    )
    # Exactly one coalesced delta at source-exhaustion drain.
    assert len(out) == 1
    assert out[0].event == "delta"
    assert out[0].delta_text == "abcde"


@pytest.mark.asyncio
async def test_window_flush_when_age_exceeds_window():
    """If deltas span longer than window_ms, the buffer is flushed mid-stream.

    Two chunks 100ms apart with window_ms=30 → buffer flushes between them.
    """
    items = [
        _delta("hello", delay_ms=0),
        _delta("world", delay_ms=100),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=30.0, idle_ms=500.0)
    )
    # Two events: one flushed when window expired, one drained at end.
    assert len(out) == 2
    assert out[0].delta_text == "hello"
    assert out[1].delta_text == "world"


# -- idle flush -------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_flush_drains_buffer_without_more_events():
    """Pending buffer must NOT be held forever if the source goes quiet."""
    # First chunk arrives at t=0; second one at t=300ms. With
    # idle_ms=100 and window_ms=500, the first event is held in buffer
    # until idle expiry flushes it well before the second chunk.
    items = [
        _delta("first", delay_ms=0),
        _delta("second", delay_ms=300),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=500.0, idle_ms=100.0)
    )
    # First chunk flushed on idle; second chunk drained at exhaustion.
    assert len(out) == 2
    assert out[0].delta_text == "first"
    assert out[1].delta_text == "second"


# -- passthrough for non-coalescable events ---------------------------------


@pytest.mark.asyncio
async def test_tool_use_event_flushes_buffer_then_passes_through():
    items = [
        (LlmStreamEvent(event="delta", delta_text="hi"), 0.0),
        (LlmStreamEvent(event="tool_use_start", metadata={"name": "search"}), 0.0),
        (LlmStreamEvent(event="delta", delta_text="ok"), 0.0),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=500.0, idle_ms=500.0)
    )
    assert len(out) == 3
    assert out[0].event == "delta" and out[0].delta_text == "hi"
    assert out[1].event == "tool_use_start"
    assert out[2].event == "delta" and out[2].delta_text == "ok"


@pytest.mark.asyncio
async def test_done_event_drains_pending_buffer_first():
    items = [
        (LlmStreamEvent(event="delta", delta_text="partial"), 0.0),
        (
            LlmStreamEvent(
                event="done",
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
            0.0,
        ),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=500.0, idle_ms=500.0)
    )
    assert len(out) == 2
    assert out[0].delta_text == "partial"
    assert out[1].event == "done"
    assert out[1].finish_reason == LlmFinishReason.STOP


@pytest.mark.asyncio
async def test_finish_reason_carrier_passes_through():
    """A 'delta' event WITH a finish_reason is the model's stop signal —
    treat as non-coalescable so consumers see it promptly."""
    items = [
        (LlmStreamEvent(event="delta", delta_text="ab"), 0.0),
        (
            LlmStreamEvent(
                event="delta",
                delta_text="",
                finish_reason=LlmFinishReason.STOP,
            ),
            0.0,
        ),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=500.0, idle_ms=500.0)
    )
    # Buffer-flush ("ab") + finish-reason event passes through.
    assert len(out) == 2
    assert out[0].delta_text == "ab"
    assert out[1].finish_reason == LlmFinishReason.STOP


# -- reasoning channel ------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_delta_not_coalesced():
    """Live-thinking channel preserves its natural cadence so the operator
    sees the model's reasoning trace in real time."""
    items = [
        (LlmStreamEvent(event="delta", delta_text="answer "), 0.0),
        (LlmStreamEvent(event="delta", delta_text="", delta_reasoning="hmm"), 0.0),
        (LlmStreamEvent(event="delta", delta_text="more"), 0.0),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=500.0, idle_ms=500.0)
    )
    # First delta flushed before reasoning passes through; reasoning
    # event passes through verbatim; trailing "more" emitted on drain.
    assert len(out) == 3
    assert out[0].delta_text == "answer "
    assert out[1].delta_reasoning == "hmm"
    assert out[2].delta_text == "more"


# -- edge cases -------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_source_yields_nothing():
    out = await _collect(
        coalesce_stream(_drive([]), window_ms=80.0, idle_ms=200.0)
    )
    assert out == []


@pytest.mark.asyncio
async def test_producer_exception_propagated_after_buffer_drain():
    """If the source raises mid-stream, the consumer must still see any
    accumulated buffer before the exception bubbles up."""

    async def bad_source() -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(event="delta", delta_text="kept")
        raise RuntimeError("simulated provider failure")

    out: list[LlmStreamEvent] = []
    with pytest.raises(RuntimeError, match="simulated provider failure"):
        async for event in coalesce_stream(
            bad_source(), window_ms=500.0, idle_ms=500.0
        ):
            out.append(event)
    # Buffer was drained before the error surfaced.
    assert len(out) == 1
    assert out[0].delta_text == "kept"


@pytest.mark.asyncio
async def test_invalid_idle_ms_raises():
    with pytest.raises(ValueError):

        async def _consume():
            async for _ in coalesce_stream(_drive([]), idle_ms=0):
                pass

        await _consume()


@pytest.mark.asyncio
async def test_metadata_from_last_buffered_delta_carried_through():
    """When the buffer is flushed, the metadata from the LAST delta that
    went into the buffer is preserved on the coalesced event — useful for
    callers that pass span/trace IDs through the stream."""
    items = [
        (LlmStreamEvent(event="delta", delta_text="x", metadata={"span": "a"}), 0.0),
        (LlmStreamEvent(event="delta", delta_text="y", metadata={"span": "b"}), 0.0),
    ]
    out = await _collect(
        coalesce_stream(_drive(items), window_ms=500.0, idle_ms=500.0)
    )
    assert len(out) == 1
    assert out[0].delta_text == "xy"
    assert out[0].metadata == {"span": "b"}
