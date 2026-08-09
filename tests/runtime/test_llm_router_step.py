"""R8 — async-router step-loop integration: classify once per run, cache the role, and
have the sync build path reuse it via pre_resolved_model_role."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.model_router import LlmDifficultyRouter
from agent_driver.runtime.single_agent.llm_step import _maybe_llm_route, _run_user_text
from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)


class _FakeProvider:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.name = "fake"
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return LlmResponse(
            message=ChatMessage(role="assistant", content=self.verdict),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(),
            provider="fake",
            model="router-model",
            metadata={},
        )


def _run(text):
    return AgentRunInput(input=text, agent_id="a", graph_preset="single_react")


def _host(router):
    return SimpleNamespace(_config=SimpleNamespace(model_router=router))


def _context(text, *, step=0, metadata=None):
    return SimpleNamespace(
        metadata=metadata if metadata is not None else {},
        run_input=_run(text),
        llm_step_count=step,
    )


# --- _run_user_text -----------------------------------------------------------------


def test_run_user_text_from_input():
    assert _run_user_text(_run("hello there")) == "hello there"


def test_run_user_text_from_messages_when_no_input():
    ri = AgentRunInput(
        messages=[
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="the question"),
        ],
        agent_id="a",
        graph_preset="single_react",
    )
    assert _run_user_text(ri) == "the question"


# --- _maybe_llm_route ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_caches_role_once_per_run():
    provider = _FakeProvider("STRONG")
    router = LlmDifficultyRouter(provider=provider, model="router-model")
    context = _context("plan the migration")
    await _maybe_llm_route(_host(router), context)
    assert context.metadata["llm_routed_role"] == "strong"
    assert provider.calls == 1
    # second step: already cached → no extra classify call
    await _maybe_llm_route(_host(router), context)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_noop_without_async_router():
    context = _context("hi")
    # a sync-only router has no ``aroute`` → the pre-route is a no-op
    await _maybe_llm_route(SimpleNamespace(_config=SimpleNamespace(model_router=object())), context)
    assert "llm_routed_role" not in context.metadata


@pytest.mark.asyncio
async def test_error_is_swallowed_and_leaves_role_unset():
    class Boom:
        async def aroute(self, ctx):
            raise RuntimeError("x")

    context = _context("hi")
    await _maybe_llm_route(_host(Boom()), context)
    assert "llm_routed_role" not in context.metadata


# --- build consumes the cached role -------------------------------------------------


class _EmptyRegistry:
    def list_registered(self) -> Iterator[SimpleNamespace]:
        return iter(())


def test_build_uses_pre_resolved_role():
    ctx = LlmRequestBuildContext(
        run_input=_run("go"),
        registry=_EmptyRegistry(),
        max_chars=4000,
        max_messages=10,
        model_role_map={"strong": "big-model"},
        pre_resolved_model_role="strong",
    )
    req, _ = build_single_agent_llm_request(ctx)
    assert req.model_role == "strong"
    assert req.model == "big-model"
