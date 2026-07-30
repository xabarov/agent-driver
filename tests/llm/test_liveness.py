"""Epic 041 A: idle-bounded side/aux completion."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import AsyncIterator

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
)
from agent_driver.llm.liveness import AuxIdleTimeout, bounded_side_completion


def _req() -> LlmRequest:
    return LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")


def _complete_response(text: str) -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=text),
        finish_reason=LlmFinishReason.STOP,
        usage=UsageSummary(model_provider="fake", model_name="m"),
        provider="fake",
        model="m",
    )


class _StreamProvider:
    """Fake provider whose stream yields chunks with a controllable gap."""

    name = "fake"

    def __init__(self, chunks: list[str], *, gap: float = 0.0, stall_after: int | None = None):
        self._chunks = chunks
        self._gap = gap
        self._stall_after = stall_after
        self.complete_calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.complete_calls += 1
        return _complete_response("".join(self._chunks))

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        for index, chunk in enumerate(self._chunks):
            if self._stall_after is not None and index == self._stall_after:
                await asyncio.sleep(3600)  # stall forever
            if self._gap:
                await asyncio.sleep(self._gap)
            yield LlmStreamEvent(event="delta", delta_text=chunk)
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="m"),
        )


@pytest.mark.asyncio
async def test_none_timeout_is_plain_complete_passthrough() -> None:
    provider = _StreamProvider(["a", "b"])
    out = await bounded_side_completion(provider, _req(), idle_timeout_seconds=None)
    assert out.message.content == "ab"
    assert provider.complete_calls == 1  # went through complete, not stream


@pytest.mark.asyncio
async def test_streams_and_reaggregates_text() -> None:
    provider = _StreamProvider(["Hel", "lo ", "world"])
    out = await bounded_side_completion(provider, _req(), idle_timeout_seconds=1.0)
    assert out.message.content == "Hello world"
    assert out.finish_reason == LlmFinishReason.STOP
    assert out.metadata.get("aux_stream_reaggregated") is True
    assert provider.complete_calls == 0  # went through stream


@pytest.mark.asyncio
async def test_stalled_stream_raises_aux_idle_timeout() -> None:
    provider = _StreamProvider(["a", "b", "c"], stall_after=1)
    with pytest.raises(AuxIdleTimeout) as excinfo:
        await bounded_side_completion(provider, _req(), idle_timeout_seconds=0.05)
    assert excinfo.value.elapsed_chunks == 1  # one chunk arrived before the stall


@pytest.mark.asyncio
async def test_slow_but_healthy_stream_survives() -> None:
    # Each chunk arrives after 0.03s; idle window is 0.1s → never idles out even
    # though the total run (0.15s) exceeds a single idle window.
    provider = _StreamProvider(["a", "b", "c", "d", "e"], gap=0.03)
    out = await bounded_side_completion(provider, _req(), idle_timeout_seconds=0.1)
    assert out.message.content == "abcde"


@pytest.mark.asyncio
async def test_non_streaming_provider_falls_back_to_complete() -> None:
    provider = SimpleNamespace(
        name="nostream",
        complete=None,
    )
    calls = {"n": 0}

    async def _complete(request: LlmRequest) -> LlmResponse:
        calls["n"] += 1
        return _complete_response("done")

    provider.complete = _complete
    out = await bounded_side_completion(provider, _req(), idle_timeout_seconds=0.5)
    assert out.message.content == "done"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_total_ceiling_bounds_a_stalled_non_streaming_complete() -> None:
    async def _hang(request: LlmRequest) -> LlmResponse:
        await asyncio.sleep(3600)
        return _complete_response("never")

    provider = SimpleNamespace(name="hang", complete=_hang)
    # No stream attr → fallback path, bounded by total ceiling = max(600, 4*idle);
    # override the ceiling small so the test is fast.
    with pytest.raises(asyncio.TimeoutError):
        await bounded_side_completion(
            provider,
            _req(),
            idle_timeout_seconds=0.02,
            total_ceiling_seconds=0.05,
        )
