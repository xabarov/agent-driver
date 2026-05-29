"""Tests for offline gamma fake provider contract (no full agent loop)."""

from __future__ import annotations

import pytest

from agent_driver.cli.evals import _EvalGammaStdlibFakeProvider
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest


@pytest.mark.asyncio
async def test_gamma_fake_provider_three_turn_contract() -> None:
    """Provider should expose scipy attempt, stdlib retry, then final answer."""
    provider = _EvalGammaStdlibFakeProvider()
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="gamma moments")],
        model="fake",
    )
    first = await provider.complete(request)
    assert first.finish_reason == LlmFinishReason.TOOL_CALLS
    planned = first.metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    first_code = str(planned[0].get("args", {}).get("code") or planned[0]["args"]["code"])
    assert "scipy" in first_code

    second = await provider.complete(request)
    assert second.finish_reason == LlmFinishReason.TOOL_CALLS
    planned2 = second.metadata.get("planned_tool_calls")
    assert isinstance(planned2, list) and planned2
    second_code = str(planned2[0].get("args", {}).get("code") or planned2[0]["args"]["code"])
    assert "import math" in second_code
    assert "scipy" not in second_code

    third = await provider.complete(request)
    assert third.finish_reason == LlmFinishReason.STOP
    assert "sandbox" in (third.message.content or "").lower()
