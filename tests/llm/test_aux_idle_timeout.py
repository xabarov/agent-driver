"""Epic 041 B: aux_completion honors the liveness idle timeout; config threads through."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.aux import aux_completion
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, LlmStreamEvent
from agent_driver.llm.liveness import AuxIdleTimeout
from agent_driver.runtime import RunnerConfig


def test_config_threads_aux_idle_timeout_through_flat_kwarg() -> None:
    assert RunnerConfig().aux_idle_timeout_seconds is None
    assert RunnerConfig(aux_idle_timeout_seconds=7.5).aux_idle_timeout_seconds == 7.5


class _StallingProvider:
    name = "fake"

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            message=ChatMessage(role="assistant", content="unbounded"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="m"),
            provider="fake",
            model="m",
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(event="delta", delta_text="partial")
        await asyncio.sleep(3600)  # stall


@pytest.mark.asyncio
async def test_aux_completion_without_timeout_is_unbounded() -> None:
    provider = _StallingProvider()
    out = await aux_completion(
        provider=provider,
        messages=[ChatMessage(role="user", content="hi")],
        model="m",
    )
    # No idle timeout → plain complete, never touches the stalling stream.
    assert out.message.content == "unbounded"


@pytest.mark.asyncio
async def test_aux_completion_with_idle_timeout_trips_on_stall() -> None:
    provider = _StallingProvider()
    with pytest.raises(AuxIdleTimeout):
        await aux_completion(
            provider=provider,
            messages=[ChatMessage(role="user", content="hi")],
            model="m",
            idle_timeout_seconds=0.05,
        )
