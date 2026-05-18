"""Offline tests for deterministic fake provider."""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.fake import FakeProvider


@pytest.mark.asyncio
async def test_fake_provider_complete_returns_deterministic_response() -> None:
    """Fake provider should return configured deterministic text."""
    provider = FakeProvider(response_text="hello from fake")
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="hello")], model="fake-model"
    )
    response = await provider.complete(request)

    assert response.message.content == "hello from fake"
    assert response.provider == "fake"
    assert response.model == "fake-model"
    assert response.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_fake_provider_stream_yields_chunks_and_done() -> None:
    """Fake provider should stream deterministic chunks and terminal done event."""
    provider = FakeProvider(response_text="stream text")
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="go")], model="fake-stream"
    )
    events = [event async for event in provider.stream(request)]

    assert len(events) >= 1
    assert any(event.delta_text for event in events)
    assert events[-1].event == "done"
    assert events[-1].usage is not None
