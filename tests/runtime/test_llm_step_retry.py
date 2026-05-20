"""Provider timeout retry in LLM step."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.runtime.single_agent.llm_step import _complete_request


@pytest.mark.asyncio
async def test_complete_request_retries_once_on_read_timeout() -> None:
    provider = SimpleNamespace(name="retry-test", calls=0)

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls += 1
        if provider.calls == 1:
            raise httpx.ReadTimeout("timed out")
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="retry", model_name="test"),
            provider="retry",
            model="test",
        )

    provider.complete = complete
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider),
        _context=SimpleNamespace(
            run_input=SimpleNamespace(stream=False, app_metadata={}),
        ),
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")])
    response = await _complete_request(host, host._context, request)
    assert response.message.content == "ok"
    assert provider.calls == 2
