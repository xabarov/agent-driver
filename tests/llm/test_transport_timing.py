"""Transport measurements are optional, isolated, ordered and content-free."""

import json

import httpx
import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.base import HttpClientConfig
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_transport_timing_is_opt_in_and_does_not_retain_content(enabled):
    calls = 0

    def handle(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, headers={"retry-after": "0"})
        chunks = [
            {"id": "gen-example", "choices": [{"delta": {"reasoning": "PRIVATE"}}]},
            {
                "id": "gen-example",
                "choices": [{"delta": {"content": "ANSWER"}, "finish_reason": "stop"}],
            },
        ]
        return httpx.Response(
            200,
            content="".join("data: " + json.dumps(row) + "\n\n" for row in chunks)
            + "data: [DONE]\n\n",
        )

    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="test",
            base_url="https://example.test",
            api_key="SECRET",
            model="test",
            http_client_config=HttpClientConfig(transport=httpx.MockTransport(handle)),
            transport_diagnostics=enabled,
        )
    )
    events = [
        event
        async for event in provider.stream(
            LlmRequest(messages=[ChatMessage(role="user", content="PROMPT")])
        )
    ]
    assert "".join(event.delta_text or "" for event in events) == "ANSWER"
    assert calls == 2
    diagnostics = [
        event.metadata["provider_transport"]
        for event in events
        if "provider_transport" in event.metadata
    ]
    assert len(diagnostics) == int(enabled)
    if enabled:
        timing = diagnostics[0]
        assert timing["generation_id"] == "gen-example"
        assert [attempt["status_code"] for attempt in timing["attempts"]] == [503, 200]
        assert (
            timing["attempts"][0]["started_ms"]
            <= timing["attempts"][0]["headers_ms"]
            <= timing["attempts"][1]["started_ms"]
        )
        assert (
            timing["attempts"][1]["headers_ms"]
            <= timing["first_data_ms"]
            <= timing["first_reasoning_ms"]
            <= timing["first_visible_ms"]
            <= timing["finished_ms"]
        )
        assert timing["data_chunks"] == 2
        assert all(
            value not in json.dumps(timing)
            for value in [
                "PRIVATE",
                "ANSWER",
                "SECRET",
                "PROMPT",
                "started_at_monotonic",
            ]
        )
