"""Tests for runner streaming mode and token delta durability."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ChatMessage,
    RuntimeEventType,
    UsageSummary,
)
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    InMemoryCheckpointStore,
    InMemoryEventLog,
    SingleAgentRunner,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.single_agent.llm_step import (
    _force_final_answer_message,
    _recover_force_final_stream_response,
)
from agent_driver.runtime.single_agent.streaming import _append_reasoning_details
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


@pytest.mark.asyncio
async def test_runner_stream_mode_emits_token_delta_events() -> None:
    """Runner should emit durable token_delta events in stream mode."""
    event_log = InMemoryEventLog()
    runner = SingleAgentRunner(
        provider=FakeProvider(response_text="stream output"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
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
    completed = [
        event
        for event in event_log.list_for_run("run_stream_mode_1")
        if event.type == RuntimeEventType.RUN_COMPLETED
    ]
    assert completed
    assert completed[-1].payload.get("answer") == "stream output"


class _FailingStreamProvider:
    """Test provider that fails at configurable point in stream."""

    def __init__(self, *, fail_after_first_token: bool) -> None:
        self._fail_after_first_token = fail_after_first_token
        self.complete_calls = 0
        self.stream_calls = 0
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
        self.complete_calls += 1
        return LlmResponse(
            message=ChatMessage(role="assistant", content="fallback answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(),
            provider=self.name,
            model=request.model or "failing-model",
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.stream_calls += 1
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


class _EmptyHeartbeatStreamProvider(_FailingStreamProvider):
    """Test provider that keeps yielding empty stream events forever."""

    def __init__(self) -> None:
        super().__init__(fail_after_first_token=False)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        while True:
            await asyncio.sleep(0.002)
            yield LlmStreamEvent(event="delta")


class _NeverYieldsStreamProvider(_FailingStreamProvider):
    """Test provider whose stream wedges before yielding any event."""

    def __init__(self) -> None:
        super().__init__(fail_after_first_token=False)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.stream_calls += 1
        await asyncio.sleep(10)
        yield LlmStreamEvent(event="done", finish_reason=LlmFinishReason.STOP)


class _ToolIntentDropStreamProvider(_FailingStreamProvider):
    """Test provider that drops after text plus streamed tool intent."""

    def __init__(self) -> None:
        super().__init__(fail_after_first_token=False)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.stream_calls += 1
        yield LlmStreamEvent(event="token", delta_text="partial before tool")
        yield LlmStreamEvent(
            event="tool_call",
            metadata={
                "planned_tool_calls": [
                    {
                        "tool_name": "web_search",
                        "args": {"query": "example"},
                        "tool_call_id": "call_stream_1",
                    }
                ]
            },
        )
        raise RuntimeError("stream dropped mid-tool-call")


class _CaptureHost:
    """Minimal host stub that records EventSpec instances."""

    def __init__(self) -> None:
        self.events: list[EventSpec] = []
        self._deps = SimpleNamespace(provider=SimpleNamespace(name="failing-stream"))

    def _emit(self, event: EventSpec) -> None:
        self.events.append(event)


def _force_final_stream_context(content: str) -> RunContext:
    return RunContext(
        run_input=AgentRunInput(
            input="write final",
            run_id="run_recover_partial_final",
            agent_id="agent",
            graph_preset="single_react",
        ),
        identifiers={
            "run_id": "run_recover_partial_final",
            "attempt_id": "att_recover_partial_final",
        },
        metadata={
            "force_final_answer": True,
            "assistant_stream_started": True,
            "assistant_stream_completed": False,
            "assistant_stream_content": content,
        },
    )


@pytest.mark.asyncio
async def test_runner_stream_failure_before_output_falls_back_to_non_stream() -> None:
    """Opening stream failures should retry once without streaming."""
    event_log = InMemoryEventLog()
    provider = _FailingStreamProvider(fail_after_first_token=False)
    runner = SingleAgentRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )

    output = await runner.run(
        AgentRunInput(
            input="stream fail before output",
            run_id="run_stream_open_failure_fallback",
            agent_id="agent",
            graph_preset="single_react",
            stream=True,
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "fallback answer"
    assert provider.stream_calls == 1
    assert provider.complete_calls == 1
    events = event_log.list_for_run("run_stream_open_failure_fallback")
    assert RuntimeEventType.RUN_FAILED not in [event.type for event in events]
    assert any(
        event.type == RuntimeEventType.WARNING
        and event.payload.get("signal_id") == "provider_stream_non_stream_fallback"
        and event.payload.get("provider_diagnostics", {}).get("stream_events_seen") == 0
        for event in events
    )
    assert any(
        event.type == RuntimeEventType.ASSISTANT_MESSAGE_REPLACED
        and event.payload.get("replacement_reason")
        == "provider_stream_non_stream_fallback"
        and event.payload.get("content") == "fallback answer"
        for event in events
    )


@pytest.mark.asyncio
async def test_runner_stream_failure_emits_terminal_failure_event() -> None:
    """Stream failures should emit run_failed and keep last checkpoint on run_started."""
    event_log = InMemoryEventLog()
    checkpoint_store = InMemoryCheckpointStore()
    provider = _FailingStreamProvider(fail_after_first_token=True)
    runner = SingleAgentRunner(
        provider=provider,
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
    assert provider.complete_calls == 0
    assert RuntimeEventType.TOKEN_DELTA in event_types
    assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED in event_types
    failed_event = next(
        event for event in events if event.type == RuntimeEventType.RUN_FAILED
    )
    diagnostics = failed_event.payload.get("stream_diagnostics")
    assert diagnostics["exception_type"] == "RuntimeError"
    assert diagnostics["stream_events_seen"] == 1
    assert diagnostics["token_chunks_seen"] == 1
    assert diagnostics["assistant_stream_tombstoned"] is True
    latest = checkpoint_store.latest("run_stream_failure")
    assert latest is not None
    assert latest.state.metadata.get("next_step") == "llm_call"


def test_force_final_stream_failure_recovers_long_partial_answer() -> None:
    """Late provider errors after forced final text should keep the answer."""
    host = _CaptureHost()
    content = "Финальный ответ. " * 20
    context = _force_final_stream_context(content)

    response = _recover_force_final_stream_response(
        host,
        context,
        reason="provider_stream_error",
    )

    assert response is not None
    assert response.message.content == content
    assert context.metadata["assistant_stream_recovered"] is True
    event_types = [event.event_type for event in host.events]
    assert RuntimeEventType.WARNING in event_types
    assert RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED in event_types
    assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED not in event_types


def test_force_final_stream_drop_after_partial_final_answer_marks_recovery() -> None:
    """A provider drop after substantial final text is a recovered final answer."""
    host = _CaptureHost()
    content = "Итоговый ответ уже был передан пользователю. " * 8
    context = _force_final_stream_context(content)

    response = _recover_force_final_stream_response(
        host,
        context,
        reason="provider_stream_error",
    )

    assert response is not None
    assert response.message.content == content
    assert response.metadata["provider_stream_partial_final_recovered"] is True
    assert response.metadata["transition_reason"] == "provider_stream_error"
    warning = next(
        event
        for event in host.events
        if event.event_type == RuntimeEventType.WARNING
    )
    assert warning.payload["signal_id"] == "provider_stream_partial_final_recovered"
    completed = next(
        event
        for event in host.events
        if event.event_type == RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED
    )
    assert completed.payload["recovered_partial"] is True
    assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED not in [
        event.event_type for event in host.events
    ]


def test_streaming_reasoning_details_merge_text_deltas() -> None:
    details: list[object] = []

    _append_reasoning_details(
        details,
        [
            {
                "type": "reasoning.summary",
                "summary": "Need",
                "format": "openai-responses-v1",
                "index": 0,
            },
            {
                "type": "reasoning.summary",
                "summary": " fetch",
                "format": "openai-responses-v1",
                "index": 0,
            },
            {
                "type": "reasoning.encrypted",
                "data": "opaque",
                "format": "openai-responses-v1",
                "id": "rs_1",
                "index": 0,
            },
        ],
    )

    assert details == [
        {
            "type": "reasoning.summary",
            "summary": "Need fetch",
            "format": "openai-responses-v1",
            "index": 0,
        },
        {
            "type": "reasoning.encrypted",
            "data": "opaque",
            "format": "openai-responses-v1",
            "id": "rs_1",
            "index": 0,
        },
    ]


def test_force_final_stream_failure_does_not_recover_short_partial_answer() -> None:
    """Tiny partials are still treated as failed streams."""
    host = _CaptureHost()
    context = _force_final_stream_context("partial")

    response = _recover_force_final_stream_response(
        host,
        context,
        reason="provider_stream_error",
    )

    assert response is None
    assert not host.events


def test_force_final_stream_failure_does_not_recover_when_tool_intent_pending() -> None:
    """Substantial partial text must not be promoted if a tool call was pending."""
    host = _CaptureHost()
    context = _force_final_stream_context("Финальный ответ. " * 20)
    context.metadata["assistant_stream_tool_intent_seen"] = True

    response = _recover_force_final_stream_response(
        host,
        context,
        reason="provider_stream_error",
    )

    assert response is None
    assert "assistant_stream_recovered" not in context.metadata
    assert not host.events


def test_force_final_message_includes_fetched_sources_for_verified_research() -> None:
    """Final-only repair prompt should hand the model concrete source URLs."""
    context = _force_final_stream_context("Финальный ответ. " * 20)
    context.run_input.tool_policy.metadata["task_contract"] = {
        "kind": "research",
        "requires_research": True,
        "research_depth": "source_verified_report",
    }
    context.metadata["tool_results"] = [
        {
            "call": {
                "tool_name": "web_fetch",
                "tool_call_id": "call_a",
                "args": {"url": "https://example.com/a"},
            },
            "structured_output": {
                "url": "https://example.com/a",
                "metadata": {"title": "Example A"},
            },
        }
    ]

    message = _force_final_answer_message(context)

    assert "Markdown links" in message
    assert "Example A: https://example.com/a" in message


def test_force_final_message_uses_deep_research_artifact_handoff() -> None:
    context = _force_final_stream_context("Финальный ответ. " * 20)
    context.metadata["deep_research_artifacts"] = {
        "report_exists": True,
        "report_path": "research/report.md",
        "source_ledger_path": "research/sources.jsonl",
    }

    message = _force_final_answer_message(context)

    assert "research/report.md" in message
    assert "research/sources.jsonl" in message
    assert "do not paste or rewrite the full report in chat" in message


@pytest.mark.asyncio
async def test_runner_stream_wedge_before_output_falls_back_to_non_stream() -> None:
    """A stream that never yields anything should fall back to non-stream once."""
    event_log = InMemoryEventLog()
    provider = _NeverYieldsStreamProvider()
    runner = SingleAgentRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )

    output = await runner.run(
        AgentRunInput(
            input="stream wedges before output",
            run_id="run_stream_wedge_fallback",
            agent_id="agent",
            graph_preset="single_react",
            stream=True,
            app_metadata={"llm_stream_idle_timeout_seconds": 0.01},
        )
    )

    assert output.status.value == "completed"
    assert output.answer == "fallback answer"
    assert provider.stream_calls == 1
    assert provider.complete_calls == 1
    events = event_log.list_for_run("run_stream_wedge_fallback")
    assert RuntimeEventType.RUN_FAILED not in [event.type for event in events]
    warning = next(
        event
        for event in events
        if event.type == RuntimeEventType.WARNING
        and event.payload.get("signal_id") == "provider_stream_non_stream_fallback"
    )
    diagnostics = warning.payload["provider_diagnostics"]
    assert diagnostics["transition_reason"] == "stream_idle_timeout"
    assert diagnostics["stream_events_seen"] == 0
    assert diagnostics["idle_timeout_seconds"] == 0.01


@pytest.mark.asyncio
async def test_runner_stream_drop_mid_tool_call_tombstones_partial_text() -> None:
    """A stream drop after tool intent should fail, not fall back or recover."""
    event_log = InMemoryEventLog()
    provider = _ToolIntentDropStreamProvider()
    runner = SingleAgentRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )

    with pytest.raises(RuntimeExecutionError):
        await runner.run(
            AgentRunInput(
                input="stream drops mid tool",
                run_id="run_stream_drop_mid_tool",
                agent_id="agent",
                graph_preset="single_react",
                stream=True,
            )
        )

    assert provider.complete_calls == 0
    events = event_log.list_for_run("run_stream_drop_mid_tool")
    event_types = [event.type for event in events]
    assert RuntimeEventType.TOKEN_DELTA in event_types
    assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED in event_types
    failed_event = next(
        event for event in events if event.type == RuntimeEventType.RUN_FAILED
    )
    diagnostics = failed_event.payload["stream_diagnostics"]
    assert diagnostics["exception_message"] == "stream dropped mid-tool-call"
    assert diagnostics["assistant_stream_tool_intent_seen"] is True
    assert diagnostics["assistant_stream_tombstoned"] is True
    assert diagnostics["stream_events_seen"] == 2
    assert diagnostics["token_chunks_seen"] == 1


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
    assert RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED in [
        event.type for event in events
    ]
    assert any(
        event.type == RuntimeEventType.RUN_FAILED
        and event.payload.get("reason") == "model_error"
        and event.payload.get("transition_reason") == "stream_idle_timeout"
        and event.payload.get("stream_diagnostics", {}).get("idle_timeout_seconds")
        == 0.01
        and event.payload.get("stream_diagnostics", {}).get("token_chunks_seen") == 1
        for event in events
    )


@pytest.mark.asyncio
async def test_runner_stream_idle_timeout_ignores_empty_heartbeats() -> None:
    """Empty provider chunks must not keep the assistant pending forever."""
    event_log = InMemoryEventLog()
    runner = SingleAgentRunner(
        provider=_EmptyHeartbeatStreamProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    with pytest.raises(RuntimeExecutionError):
        await runner.run(
            AgentRunInput(
                input="stream empty heartbeats",
                run_id="run_stream_empty_heartbeat_timeout",
                agent_id="agent",
                graph_preset="single_react",
                stream=True,
                app_metadata={"llm_stream_idle_timeout_seconds": 0.01},
            )
        )

    events = event_log.list_for_run("run_stream_empty_heartbeat_timeout")
    assert RuntimeEventType.TOKEN_DELTA not in [event.type for event in events]
    assert any(
        event.type == RuntimeEventType.RUN_FAILED
        and event.payload.get("transition_reason") == "stream_idle_timeout"
        and event.payload.get("stream_diagnostics", {}).get("stream_events_seen") >= 1
        and event.payload.get("stream_diagnostics", {}).get("token_chunks_seen") == 0
        for event in events
    )
