"""Tests for offline scientific python fake provider contracts."""

from __future__ import annotations

import pytest

from agent_driver.cli.evals import (
    _EvalGammaScipyFakeProvider,
    _EvalPandasLinalgFakeProvider,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest


@pytest.mark.asyncio
async def test_gamma_scipy_fake_provider_contract() -> None:
    provider = _EvalGammaScipyFakeProvider()
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="gamma scipy")],
        model="fake",
    )
    first = await provider.complete(request)
    assert first.finish_reason == LlmFinishReason.TOOL_CALLS
    planned = first.metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    first_code = str(planned[0].get("args", {}).get("code") or planned[0]["args"]["code"])
    assert "scipy.stats" in first_code

    second = await provider.complete(request)
    assert second.finish_reason == LlmFinishReason.TOOL_CALLS
    planned2 = second.metadata.get("planned_tool_calls")
    assert isinstance(planned2, list) and planned2
    second_code = str(planned2[0].get("args", {}).get("code") or planned2[0]["args"]["code"])
    assert "gamma.cdf" in second_code

    third = await provider.complete(request)
    assert third.finish_reason == LlmFinishReason.STOP
    assert "0.82" in (third.message.content or "")


@pytest.mark.asyncio
async def test_pandas_linalg_fake_provider_contract() -> None:
    provider = _EvalPandasLinalgFakeProvider()
    request = LlmRequest(
        messages=[ChatMessage(role="user", content="solve 2x2")],
        model="fake",
    )
    first = await provider.complete(request)
    assert first.finish_reason == LlmFinishReason.TOOL_CALLS
    planned = first.metadata.get("planned_tool_calls")
    assert isinstance(planned, list) and planned
    code = str(planned[0].get("args", {}).get("code") or planned[0]["args"]["code"])
    assert "numpy" in code
    assert "linalg.solve" in code

    second = await provider.complete(request)
    assert second.finish_reason == LlmFinishReason.STOP
    assert "2" in (second.message.content or "")
    assert "1.5" in (second.message.content or "")
