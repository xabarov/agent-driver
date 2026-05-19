"""Shared streaming helpers for provider adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_driver.llm.contracts import LlmFinishReason, LlmStreamEvent, UsageSummary


async def stream_text_chunks(
    text: str, *, chunk_size: int = 24
) -> AsyncIterator[LlmStreamEvent]:
    """Yield deterministic text chunks followed by a stop event."""
    idx = 0
    if not text:
        yield LlmStreamEvent(
            event="delta",
            delta_text="",
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(input_tokens=0, output_tokens=0, total_tokens=0),
        )
        return

    while idx < len(text):
        next_idx = min(len(text), idx + chunk_size)
        yield LlmStreamEvent(
            event="delta", delta_text=text[idx:next_idx], finish_reason=None
        )
        idx = next_idx

    yield LlmStreamEvent(
        event="done",
        delta_text="",
        finish_reason=LlmFinishReason.STOP,
        usage=UsageSummary(
            input_tokens=0, output_tokens=len(text), total_tokens=len(text)
        ),
    )
