"""Tests for runner streaming mode and token delta durability."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from agent_driver.contracts import AgentRunInput, ChatMessage, RuntimeEventType, UsageSummary
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime import InMemoryCheckpointStore, InMemoryEventLog, SingleAgentRunner


@pytest.mark.asyncio
async def test_runner_stream_mode_emits_token_delta_events() -> None:
    """Runner should emit durable token_delta events in stream mode."""
    runner = SingleAgentRunner(
        provider=FakeProvider(response_text="stream output"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="stream this",
            run_id="run_stream_mode_1",
            agent_id="agent",
            graph_preset="single_react",
            stream=True,
        )
    )
    assert output.status.value == "completed"
    assert output.answer == "stream output"
    types = [event.type for event in output.events]
    assert RuntimeEventType.ASSISTANT_MESSAGE_STARTED in types
    assert RuntimeEventType.TOKEN_DELTA in types
    assert RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED in types
    assert types.index(RuntimeEventType.TOKEN_DELTA) < types.index(
        RuntimeEventType.LLM_CALL_COMPLETED
    )
    assert any(
        event.type == RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED
        and event.payload.get("content") == "stream output"
        for event in output.events
    )


class _FailingStreamProvider:
    """Test provider that fails at configurable point in stream."""

    def __init__(self, *, fail_after_first_token: bool) -> None:
        self._fail_after_first_token = fail_after_first_token
        self._status = ProviderStatus(
            provider_name="failing-stream",
            provider_kind=LlmProviderKind.FAKE,
        )

    @property
    def name(self) -> str:
        return "failing-stream"

    @property
    def status(self) -> ProviderStatus:
        return self._status

    async def healthcheck(self) -> ProviderStatus:
        return self._status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            message=ChatMessage(role="assistant", content="unused"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(),
            provider=self.name,
            model=request.model or "failing-model",
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if self._fail_after_first_token:
            yield LlmStreamEvent(event="token", delta_text="partial")
        raise RuntimeError("stream failure")


class _HangingStreamProvider(_FailingStreamProvider):
    """Test provider that yields once and then never reaches a done event."""

    def __init__(self) -> None:
        super().__init__(fail_after_first_token=True)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        yield LlmStreamEvent(event="token", delta_text="partial")
        await asyncio.sleep(10)
        yield LlmStreamEvent(event="done", finish_reason=LlmFinishReason.STOP)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_first_token", [False, True])
async def test_runner_stream_failure_emits_terminal_failure_event(
    fail_after_first_token: bool,
) -> None:
    """Stream failures should emit run_failed and keep last checkpoint on run_started."""
    event_log = InMemoryEventLog()
    checkpoint_store = InMemoryCheckpointStore()
    runner = SingleAgentRunner(
        provider=_FailingStreamProvider(fail_after_first_token=fail_after_first_token),
        checkpoint_store=checkpoint_store,
        event_log=event_log,
    )
    with pytest.raises(RuntimeExecutionError):
        await runner.run(
            AgentRunInput(
                input="stream fail",
                run_id="run_stream_failure",
                agent_id="agent",
                graph_preset="single_react",
                stream=True,
            )
        )
    events = event_log.list_for_run("run_stream_failure")
    event_types = [event.type for event in events]
    assert RuntimeEventType.RUN_FAILED in event_types
    if fail_after_first_token:
        assert RuntimeEventType.TOKEN_DELTA in event_types
        assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED in event_types
    else:
        assert RuntimeEventType.TOKEN_DELTA not in event_types
        assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED not in event_types
    latest = checkpoint_store.latest("run_stream_failure")
    assert latest is not None
    assert latest.state.metadata.get("next_step") == "llm_call"


@pytest.mark.asyncio
async def test_runner_stream_idle_timeout_fails_after_partial_delta() -> None:
    """Idle provider streams should fail terminally instead of leaving SSE pending."""
    event_log = InMemoryEventLog()
    runner = SingleAgentRunner(
        provider=_HangingStreamProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    with pytest.raises(RuntimeExecutionError):
        await runner.run(
            AgentRunInput(
                input="stream hangs",
                run_id="run_stream_idle_timeout",
                agent_id="agent",
                graph_preset="single_react",
                stream=True,
                app_metadata={"llm_stream_idle_timeout_seconds": 0.01},
            )
        )

    events = event_log.list_for_run("run_stream_idle_timeout")
    assert [event.type for event in events].count(RuntimeEventType.TOKEN_DELTA) == 1
    assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED in [event.type for event in events]
    assert any(
        event.type == RuntimeEventType.RUN_FAILED
        and event.payload.get("reason") == "model_error"
        and event.payload.get("transition_reason") == "stream_idle_timeout"
        for event in events
    )
