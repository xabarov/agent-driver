"""Ordered fallback-model list on the main completion path (resilience F4)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import agent_driver.runtime.single_agent.llm_step.completion as completion
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.runtime import RunnerConfig
from agent_driver.runtime.single_agent.llm_step import _complete_request


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op the in-place retry backoff so the model-fallback path runs fast."""

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(completion.asyncio, "sleep", _noop)


def _provider(fail_models: set[str], *, status: int = 503) -> SimpleNamespace:
    provider = SimpleNamespace(name="p", calls=[])

    async def complete(request: LlmRequest) -> LlmResponse:
        provider.calls.append(request.model)
        if request.model in fail_models:
            resp = httpx.Response(
                status, text="err", request=httpx.Request("POST", "https://x.test")
            )
            raise httpx.HTTPStatusError("e", request=resp.request, response=resp)
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="p", model_name=request.model or "m"),
            provider="p",
            model=request.model or "m",
        )

    provider.complete = complete
    return provider


def _host(provider: SimpleNamespace, fallback_models: tuple[str, ...]) -> tuple:
    emitted: list = []
    host = SimpleNamespace(
        _deps=SimpleNamespace(provider=provider, fallback_models=fallback_models),
        _emit=emitted.append,
    )
    context = SimpleNamespace(
        run_input=SimpleNamespace(stream=False, app_metadata={}),
        metadata={},
        run_id="r",
        attempt_id="a",
    )
    return host, context, emitted


def _request(model: str) -> LlmRequest:
    return LlmRequest(messages=[ChatMessage(role="user", content="hi")], model=model)


@pytest.mark.asyncio
async def test_falls_back_to_next_model_on_transient_error() -> None:
    provider = _provider({"m1"})  # m1 always 503, m2 succeeds
    host, context, emitted = _host(provider, ("m2",))

    response = await _complete_request(host, context, _request("m1"))

    assert response.message.content == "ok"
    assert response.model == "m2"  # the fallback model produced the answer
    assert provider.calls == ["m1", "m1", "m1", "m2"]  # 3 in-place retries, then swap
    assert context.metadata["model_fallbacks"] == 1
    assert any(e.event_type.value == "warning" for e in emitted)


@pytest.mark.asyncio
async def test_walks_the_whole_chain_until_one_succeeds() -> None:
    provider = _provider({"m1", "m2"})  # only m3 works
    host, context, _ = _host(provider, ("m2", "m3"))

    response = await _complete_request(host, context, _request("m1"))

    assert response.model == "m3"
    assert context.metadata["model_fallbacks"] == 2


@pytest.mark.asyncio
async def test_fatal_error_does_not_fall_back() -> None:
    provider = _provider({"m1"}, status=401)  # auth = fatal to rotation
    host, context, _ = _host(provider, ("m2",))

    with pytest.raises(httpx.HTTPStatusError):
        await _complete_request(host, context, _request("m1"))
    assert provider.calls == ["m1"]  # never tried the fallback model


@pytest.mark.asyncio
async def test_all_models_failing_raises_the_last_error() -> None:
    provider = _provider({"m1", "m2"})  # both transient-fail, no survivor
    host, context, _ = _host(provider, ("m2",))

    with pytest.raises(httpx.HTTPStatusError):
        await _complete_request(host, context, _request("m1"))
    assert provider.calls == ["m1", "m1", "m1", "m2", "m2", "m2"]


@pytest.mark.asyncio
async def test_no_fallback_models_is_a_single_attempt_chain() -> None:
    provider = _provider({"m1"})
    host, context, _ = _host(provider, ())  # empty → primary only

    with pytest.raises(httpx.HTTPStatusError):
        await _complete_request(host, context, _request("m1"))
    assert provider.calls == ["m1", "m1", "m1"]  # in-place retries only, no swap
    assert "model_fallbacks" not in context.metadata


def test_runner_config_carries_fallback_models() -> None:
    config = RunnerConfig(fallback_models=("a", "b"))
    assert config.fallback_models == ("a", "b")
    assert RunnerConfig().fallback_models == ()
