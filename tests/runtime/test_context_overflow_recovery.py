"""N2 — reactive compaction: complete_request recovers from CONTEXT_OVERFLOW."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.llm_step.completion import complete_request
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


def _overflow_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(
        400, request=request, text="This model's maximum context length is 8192 tokens"
    )
    return httpx.HTTPStatusError("400", request=request, response=response)


class _OverflowProvider:
    """Raises context-overflow for the first ``overflow_times`` calls."""

    name = "fake"

    def __init__(self, *, overflow_times: int) -> None:
        self.calls = 0
        self._overflow_times = overflow_times

    async def complete(self, request):  # noqa: ANN001
        self.calls += 1
        if self.calls <= self._overflow_times:
            raise _overflow_error()
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            provider="fake",
            model="m",
        )


def _host(provider) -> SimpleNamespace:  # noqa: ANN001
    return SimpleNamespace(
        _deps=SimpleNamespace(provider=provider), _emit=lambda _e: None
    )


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="r1",
        attempt_id="a1",
        metadata={},
        run_input=SimpleNamespace(stream=False, app_metadata={}),
    )


@pytest.mark.asyncio
async def test_overflow_triggers_compact_and_retry() -> None:
    provider = _OverflowProvider(overflow_times=1)
    context = _context()
    recovered: list[bool] = []

    async def recover():
        recovered.append(True)
        return "rebuilt-request"

    response = await complete_request(
        _host(provider), context, "orig-request", recover_context_overflow=recover
    )

    assert response.message.content == "ok"
    assert provider.calls == 2  # overflow, then retry
    assert recovered == [True]  # recovery ran exactly once
    assert context.metadata["context_overflow_recovery"] == "compacted_and_retried"


@pytest.mark.asyncio
async def test_overflow_recovery_is_single_shot() -> None:
    provider = _OverflowProvider(overflow_times=3)
    context = _context()
    recovered: list[bool] = []

    async def recover():
        recovered.append(True)
        return "rebuilt-request"

    with pytest.raises(httpx.HTTPStatusError):
        await complete_request(
            _host(provider), context, "orig", recover_context_overflow=recover
        )
    # Recovery fires once; the second overflow is not recovered again.
    assert recovered == [True]


@pytest.mark.asyncio
async def test_overflow_without_callback_propagates() -> None:
    provider = _OverflowProvider(overflow_times=1)
    with pytest.raises(httpx.HTTPStatusError):
        await complete_request(_host(provider), _context(), "orig")
    assert provider.calls == 1


class _OverflowOnceThenAnswer(FakeProvider):
    """A real provider that overflows once, then returns a final answer."""

    def __init__(self) -> None:
        super().__init__(response_text="recovered answer")
        self.calls = 0

    async def complete(self, request):  # noqa: ANN001
        self.calls += 1
        if self.calls == 1:
            raise _overflow_error()
        return LlmResponse(
            message=ChatMessage(role="assistant", content="recovered answer"),
            finish_reason=LlmFinishReason.STOP,
            provider="fake",
            model="m",
        )


@pytest.mark.asyncio
async def test_runner_recovers_from_overflow_end_to_end() -> None:
    """A run whose first provider call overflows compacts and completes."""
    provider = _OverflowOnceThenAnswer()
    # Compaction disabled so the recovery is a clean no-op compact + rebuild +
    # retry; the retry succeeds because the provider stops overflowing.
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        config=RunnerConfig(enable_compaction=False),
    )
    output = await agent.run(
        AgentRunInput(
            input="hello",
            run_id="run_overflow",
            thread_id="t1",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert output.status.value == "completed"
    assert output.answer == "recovered answer"
    assert provider.calls == 2  # overflow, then a successful retry
