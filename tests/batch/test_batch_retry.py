"""Retry-with-backoff on transient (raised) failures in BatchRunner."""

from __future__ import annotations

import pytest

from agent_driver.batch import BatchRunner, items_from_prompts
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


class _FlakyAgent:
    """Wraps a real agent; raises ``fail_times`` then delegates to query."""

    def __init__(self, fail_times: int) -> None:
        self._inner = create_agent(
            provider=FakeProvider(response_text="ok"), tools=ToolSet.only()
        )
        self._fail = fail_times
        self.calls = 0
        # BatchRunner reaches the cost ledger via agent only through query.

    async def query(self, text: str, *, run_id: str | None = None):  # noqa: ANN201
        self.calls += 1
        if self.calls <= self._fail:
            raise RuntimeError("transient 429")
        return await self._inner.query(text, run_id=run_id)


def _runner(agent, **kw) -> BatchRunner:
    slept: list[float] = []

    async def _sleep(s: float) -> None:
        slept.append(s)

    runner = BatchRunner(agent, concurrency=1, sleep=_sleep, **kw)
    runner._slept = slept  # type: ignore[attr-defined]
    return runner


@pytest.mark.asyncio
async def test_retries_recover_transient_failure() -> None:
    agent = _FlakyAgent(fail_times=2)
    runner = _runner(agent, retries=2, retry_backoff_s=0.1)
    report = await runner.run(items_from_prompts(["a"]))
    assert report.completed == 1
    assert agent.calls == 3  # 2 failures + 1 success
    assert runner._slept == [0.1, 0.2]  # exponential backoff between retries


@pytest.mark.asyncio
async def test_exhausted_retries_record_error() -> None:
    agent = _FlakyAgent(fail_times=5)
    runner = _runner(agent, retries=1, retry_backoff_s=0.0)
    report = await runner.run(items_from_prompts(["a"]))
    assert report.completed == 0
    assert agent.calls == 2  # initial + 1 retry, then give up


@pytest.mark.asyncio
async def test_default_no_retries() -> None:
    agent = _FlakyAgent(fail_times=1)
    runner = _runner(agent)  # retries defaults to 0
    report = await runner.run(items_from_prompts(["a"]))
    assert report.completed == 0
    assert agent.calls == 1


def test_negative_retries_rejected() -> None:
    with pytest.raises(ValueError):
        BatchRunner(
            create_agent(provider=FakeProvider(), tools=ToolSet.only()), retries=-1
        )


class _BillingAgent:
    """Always raises a 402 (non-transient) provider error."""

    def __init__(self) -> None:
        self.calls = 0

    async def query(self, text: str, *, run_id: str | None = None):  # noqa: ANN201
        import httpx

        self.calls += 1
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        resp = httpx.Response(402, request=req, text="Payment Required")
        raise httpx.HTTPStatusError("402", request=req, response=resp)


@pytest.mark.asyncio
async def test_non_transient_402_fails_fast() -> None:
    """A 402 (billing) error is not retried — it fails fast."""
    agent = _BillingAgent()
    runner = _runner(agent, retries=3, retry_backoff_s=0.0)
    report = await runner.run(items_from_prompts(["a"]))
    assert report.completed == 0
    assert agent.calls == 1  # no wasted retries on a non-transient error
    assert runner._slept == []
