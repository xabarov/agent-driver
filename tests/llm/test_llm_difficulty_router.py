"""R8 — LlmDifficultyRouter: a small model classifies the request; robust fallbacks."""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.model_router import (
    AsyncModelRouter,
    LlmDifficultyRouter,
    RouteContext,
)

_RUN = AgentRunInput(input="x", agent_id="a", graph_preset="single_react")


class _FakeProvider:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.name = "fake"

    async def complete(self, request):
        return LlmResponse(
            message=ChatMessage(role="assistant", content=self.verdict),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(),
            provider="fake",
            model="router-model",
            metadata={},
        )


class _BoomProvider:
    name = "boom"

    async def complete(self, request):
        raise RuntimeError("router provider down")


def _ctx(text: str) -> RouteContext:
    return RouteContext(
        messages=[{"role": "user", "content": text}],
        run_input=_RUN,
        default_role="default",
    )


def _router(provider, **kw) -> LlmDifficultyRouter:
    return LlmDifficultyRouter(provider=provider, model="router-model", **kw)


def test_is_async_model_router_and_has_no_sync_route():
    r = _router(_FakeProvider("SIMPLE"))
    assert isinstance(r, AsyncModelRouter)
    # No sync ``route`` → the build path skips it (it is driven from the step loop).
    assert not hasattr(r, "route")


@pytest.mark.asyncio
async def test_strong_verdict():
    assert await _router(_FakeProvider("STRONG")).aroute(_ctx("plan it")) == "strong"


@pytest.mark.asyncio
async def test_simple_verdict():
    assert await _router(_FakeProvider("SIMPLE")).aroute(_ctx("count rows")) == "simple"


@pytest.mark.asyncio
async def test_custom_roles():
    r = _router(_FakeProvider("strong"), simple_role="cheap", strong_role="smart")
    assert await r.aroute(_ctx("design a system")) == "smart"


@pytest.mark.asyncio
async def test_empty_text_returns_default_without_calling_model():
    r = _router(_BoomProvider())  # would raise if called
    empty = RouteContext(messages=[], run_input=_RUN, default_role="default")
    assert await r.aroute(empty) == "default"


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_heuristic():
    r = _router(_BoomProvider())
    assert await r.aroute(_ctx("please design and refactor this")) == "strong"
    assert await r.aroute(_ctx("hi")) == "simple"


@pytest.mark.asyncio
async def test_unparseable_verdict_falls_back_to_heuristic():
    r = _router(_FakeProvider("maybe, could be either?"))
    assert await r.aroute(_ctx("hi")) == "simple"
