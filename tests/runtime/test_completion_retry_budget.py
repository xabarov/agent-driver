"""Shared per-completion retry budget (resilience F6): bound the loop end-to-end."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import agent_driver.runtime.single_agent.llm_step.completion as completion
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.runtime import RunnerConfig
from agent_driver.runtime.single_agent.llm_step import _complete_request


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(completion.asyncio, "sleep", _noop)


def _always_503() -> SimpleNamespace:
    provider = SimpleNamespace(name="p", calls=0)

    async def complete(_request: LlmRequest):  # noqa: ANN202
        provider.calls += 1
        resp = httpx.Response(
            503, text="err", request=httpx.Request("POST", "https://x.test")
        )
        raise httpx.HTTPStatusError("e", request=resp.request, response=resp)

    provider.complete = complete
    return provider


def _host(provider: SimpleNamespace, budget: float | None) -> tuple:
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider, completion_retry_budget_seconds=budget),
        _emit=[].append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(stream=False, app_metadata={}),
        metadata={},
        run_id="r",
        attempt_id="a",
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    return host, context, request


@pytest.mark.asyncio
async def test_budget_stops_the_retry_loop_early(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clock: first read (loop start) = 0, every later read = 100 → budget of 5 is
    # already exhausted at the attempt-1 check, before a second provider call.
    reads = {"n": 0}

    def _clock() -> float:
        n = reads["n"]
        reads["n"] += 1
        return 0.0 if n == 0 else 100.0

    monkeypatch.setattr(completion, "_monotonic", _clock)
    provider = _always_503()
    host, context, request = _host(provider, budget=5.0)

    with pytest.raises(httpx.HTTPStatusError):
        await _complete_request(host, context, request)
    assert provider.calls == 1  # budget cut the loop after the first attempt


@pytest.mark.asyncio
async def test_no_budget_uses_the_full_attempt_chain() -> None:
    provider = _always_503()
    host, context, request = _host(provider, budget=None)

    with pytest.raises(httpx.HTTPStatusError):
        await _complete_request(host, context, request)
    assert provider.calls == 3  # unbounded → the plain 3-attempt loop


@pytest.mark.asyncio
async def test_generous_budget_does_not_cut_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(completion, "_monotonic", lambda: 0.0)  # time never advances
    provider = _always_503()
    host, context, request = _host(provider, budget=60.0)

    with pytest.raises(httpx.HTTPStatusError):
        await _complete_request(host, context, request)
    assert provider.calls == 3  # budget never elapses → full chain


def test_runner_config_carries_the_budget() -> None:
    assert RunnerConfig(completion_retry_budget_seconds=30.0).completion_retry_budget_seconds == 30.0
    assert RunnerConfig().completion_retry_budget_seconds is None
