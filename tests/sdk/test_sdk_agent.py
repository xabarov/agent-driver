"""Tests for app-facing SDK Agent facade."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ControlKind,
    ControlPriority,
    ControlRequest,
    RuntimeEventType,
    ToolCall,
    ToolManifest,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    UsageSummary,
)
from agent_driver.runtime import RunnerConfig
from agent_driver.runtime.control import InMemoryCommandQueueStore
from agent_driver.sdk import Agent, build_default_registry, create_agent, sdk_config_from_env
from agent_driver.tools import ToolRegistry, ToolSet


@pytest.mark.asyncio
async def test_sdk_create_agent_returns_facade_and_runs() -> None:
    """SDK create_agent should return facade that can execute runs."""
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only("web_search"))
    assert isinstance(agent, Agent)
    output = await agent.run(
        AgentRunInput(
            input="Search once.",
            run_id="run_sdk_allow",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            args={
                                "query": "agent driver",
                                "mock_results": [
                                    {"title": "A", "url": "https://example.com", "snippet": "B"}
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"


def test_sdk_control_methods_queue_commands() -> None:
    """SDK facade should expose typed steering queue helpers."""
    queue = InMemoryCommandQueueStore()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        command_queue_store=queue,
    )

    control = agent.control(
        ControlRequest(
            kind=ControlKind.INTERRUPT,
            run_id="run_sdk_control",
            priority=ControlPriority.NOW,
        )
    )
    follow_up = agent.enqueue("continue with this constraint", run_id="run_sdk_control")
    model_change = agent.set_model("openai/gpt-4.1-mini", run_id="run_sdk_control")

    pending = queue.list_pending(run_id="run_sdk_control")
    events = agent.runner.deps.event_log.list_for_run("run_sdk_control")
    assert control.ok is True
    assert follow_up.ok is True
    assert model_change.ok is True
    assert [item.kind for item in pending] == [
        ControlKind.INTERRUPT,
        ControlKind.ENQUEUE_USER_MESSAGE,
        ControlKind.SET_MODEL,
    ]
    assert any(event.type == RuntimeEventType.COMMAND_QUEUED for event in events)


def test_sdk_cancel_queued_message_marks_item_cancelled() -> None:
    """SDK facade should cancel queued command items by id."""
    queue = InMemoryCommandQueueStore()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        command_queue_store=queue,
    )
    response = agent.enqueue("later", run_id="run_sdk_cancel")

    cancelled = agent.cancel_queued_message(response.queue_id or "")

    assert cancelled.ok is True
    assert queue.list_pending(run_id="run_sdk_cancel") == []
    events = agent.runner.deps.event_log.list_for_run("run_sdk_cancel")
    assert any(event.type == RuntimeEventType.COMMAND_CANCELLED for event in events)


@pytest.mark.asyncio
async def test_sdk_stream_projects_runtime_events() -> None:
    """SDK stream should yield projected stream events."""
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only("web_search"))
    events = [
        item
        async for item in agent.stream(
            AgentRunInput(
                input="Search stream.",
                run_id="run_sdk_stream",
                agent_id="agent",
                graph_preset="single_react",
                stream=True,
                tool_policy={
                    "metadata": {
                        "planned_tool_calls": [
                            ToolCall(
                                tool_name="web_search",
                                args={
                                    "query": "agent",
                                    "mock_results": [{"title": "A", "url": "https://example.com"}],
                                },
                            ).model_dump(mode="json")
                        ]
                    }
                },
            )
        )
    ]
    assert events
    assert any(item.event == RuntimeEventType.RUN_STARTED.value for item in events)
    assert any(item.event == RuntimeEventType.TOKEN_DELTA.value for item in events)


class _SlowStreamingProvider(FakeProvider):
    async def stream(self, request: LlmRequest):
        await asyncio.sleep(0.2)
        yield LlmStreamEvent(event="delta", delta_text="slow")
        yield LlmStreamEvent(event="done", finish_reason=LlmFinishReason.STOP)


class _CaptureRequestProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="captured")
        self.last_request: LlmRequest | None = None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.last_request = request
        return await super().complete(request)


@pytest.mark.asyncio
async def test_sdk_set_model_control_affects_next_llm_request() -> None:
    """Queued set_model controls should affect the next LLM boundary."""
    provider = _CaptureRequestProvider()
    queue = InMemoryCommandQueueStore()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        command_queue_store=queue,
    )
    agent.set_model("openai/gpt-4.1-mini", run_id="run_sdk_model_control")

    await agent.run(
        AgentRunInput(
            input="hello",
            run_id="run_sdk_model_control",
            agent_id="agent",
            graph_preset="single_react",
        )
    )

    assert provider.last_request is not None
    assert provider.last_request.model == "openai/gpt-4.1-mini"
    assert queue.list_pending(run_id="run_sdk_model_control") == []
    events = agent.runner.deps.event_log.list_for_run("run_sdk_model_control")
    assert any(event.type == RuntimeEventType.CONTROL_APPLIED for event in events)


@pytest.mark.asyncio
async def test_sdk_enqueue_control_appends_user_message_at_next_llm_boundary() -> None:
    """Queued user messages should be appended before the next LLM request."""
    provider = _CaptureRequestProvider()
    queue = InMemoryCommandQueueStore()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        command_queue_store=queue,
    )
    agent.enqueue("steer this next", run_id="run_sdk_enqueue_control")

    await agent.run(
        AgentRunInput(
            input="original task",
            run_id="run_sdk_enqueue_control",
            agent_id="agent",
            graph_preset="single_react",
        )
    )

    assert provider.last_request is not None
    contents = [message.content for message in provider.last_request.messages]
    assert "original task" in contents
    assert contents[-1] == "steer this next"
    assert queue.list_pending(run_id="run_sdk_enqueue_control") == []


class _ToolLoopProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ignored")
        self.complete_calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.complete_calls += 1
        if self.complete_calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="tool-loop", model_name="loop-model"),
                provider="tool-loop",
                model="loop-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            args={
                                "query": "agent driver",
                                "mock_results": [
                                    {
                                        "title": "A",
                                        "url": "https://example.com",
                                        "snippet": "B",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="tool final answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="tool-loop", model_name="loop-model"),
            provider="tool-loop",
            model="loop-model",
            metadata={},
        )


class _EndlessToolLoopProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ignored")
        self.complete_calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.complete_calls += 1
        return LlmResponse(
            message=ChatMessage(role="assistant", content=""),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            usage=UsageSummary(model_provider="tool-loop", model_name="loop-model"),
            provider="tool-loop",
            model="loop-model",
            metadata={
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="web_search",
                        args={
                            "query": "agent driver",
                            "mock_results": [
                                {"title": "A", "url": "https://example.com", "snippet": "B"}
                            ],
                        },
                    ).model_dump(mode="json")
                ]
            },
        )


class _ProtocolCaptureProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ignored")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="tool-loop", model_name="loop-model"),
                provider="tool-loop",
                model="loop-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            tool_call_id="call_42",
                            args={
                                "query": "agent driver",
                                "mock_results": [{"title": "A", "url": "https://example.com"}],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="protocol final answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="tool-loop", model_name="loop-model"),
            provider="tool-loop",
            model="loop-model",
            metadata={},
        )


@pytest.mark.asyncio
async def test_sdk_stream_emits_incrementally_before_run_finishes() -> None:
    """First stream event should be available before full provider stream completes."""
    agent = create_agent(provider=_SlowStreamingProvider(response_text="ok"), tools=ToolSet.only())
    stream = agent.stream(
        AgentRunInput(
            input="incremental stream",
            run_id="run_sdk_incremental",
            agent_id="agent",
            graph_preset="single_react",
            stream=True,
        )
    )
    first = await asyncio.wait_for(anext(stream), timeout=0.1)
    assert first.event == RuntimeEventType.RUN_STARTED.value
    rest = [item async for item in stream]
    assert any(item.event == RuntimeEventType.TOKEN_DELTA.value for item in rest)


@pytest.mark.asyncio
async def test_sdk_request_includes_model_tool_schemas() -> None:
    """Single-agent request builder should attach selected tool schemas."""
    provider = _CaptureRequestProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    _ = await agent.run(
        AgentRunInput(
            input="Search with tools.",
            run_id="run_sdk_tools_schema",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert provider.last_request is not None
    tools = provider.last_request.tools
    assert tools
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "web_search"


@pytest.mark.asyncio
async def test_sdk_tool_stage_loops_back_to_llm_for_final_answer() -> None:
    """After planned tool calls, runtime should request final model answer."""
    provider = _ToolLoopProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="Find and summarize.",
            run_id="run_sdk_tool_loop",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert provider.complete_calls == 2
    assert output.answer == "tool final answer"
    assert any(item.tool_name == "web_search" for item in output.tool_trace)


@pytest.mark.asyncio
async def test_sdk_tool_loop_honors_max_tool_calls_limit() -> None:
    """Repeated tool-call responses should fail by max_tool_calls budget."""
    provider = _EndlessToolLoopProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="loop forever",
            run_id="run_sdk_tool_loop_limit",
            agent_id="agent",
            graph_preset="single_react",
            max_tool_calls=2,
            max_steps=8,
        )
    )
    assert output.status.value == "failed"
    assert output.terminal_reason is not None
    assert output.terminal_reason.value == "tool_policy_denied"
    assert provider.complete_calls >= 2


@pytest.mark.asyncio
async def test_sdk_stream_tool_completed_event_contains_named_tools() -> None:
    """Projected tool completion events should include per-tool payload rows."""
    provider = _ToolLoopProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    events = [
        item
        async for item in agent.stream(
            AgentRunInput(
                input="stream tools",
                run_id="run_sdk_tool_event_payload",
                agent_id="agent",
                graph_preset="single_react",
                stream=False,
                max_tool_calls=4,
                max_steps=10,
            )
        )
    ]
    completed = [item for item in events if item.event == RuntimeEventType.TOOL_CALL_COMPLETED.value]
    assert completed
    tools_payload = completed[0].data.get("tools")
    assert isinstance(tools_payload, list) and tools_payload
    assert tools_payload[0].get("tool_name") == "web_search"


@pytest.mark.asyncio
async def test_sdk_stream_tool_started_event_contains_args() -> None:
    """Projected tool start events should include serialized args payload."""
    provider = _ToolLoopProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    events = [
        item
        async for item in agent.stream(
            AgentRunInput(
                input="stream tools with args",
                run_id="run_sdk_tool_started_payload",
                agent_id="agent",
                graph_preset="single_react",
                stream=False,
                max_tool_calls=4,
                max_steps=10,
            )
        )
    ]
    started = [item for item in events if item.event == RuntimeEventType.TOOL_CALL_STARTED.value]
    assert started
    tools_payload = started[0].data.get("tools")
    assert isinstance(tools_payload, list) and tools_payload
    assert isinstance(tools_payload[0].get("args"), dict)


@pytest.mark.asyncio
async def test_sdk_followup_request_uses_tool_messages_and_none_choice() -> None:
    """Second LLM request should carry assistant tool call + tool message transcript."""
    provider = _ProtocolCaptureProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="search and summarize",
            run_id="run_sdk_tool_protocol",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=8,
            max_tool_calls=4,
        )
    )
    assert output.answer == "protocol final answer"
    assert len(provider.requests) >= 2
    followup = provider.requests[1]
    assert followup.tool_choice in (None, "auto")
    assistant_rows = [item for item in followup.messages if item.role.value == "assistant"]
    tool_rows = [item for item in followup.messages if item.role.value == "tool"]
    assert assistant_rows
    assert tool_rows
    tool_calls_payload = assistant_rows[-1].metadata.get("tool_calls")
    assert isinstance(tool_calls_payload, list) and tool_calls_payload
    assert tool_calls_payload[0].get("id") == "call_42"
    assert tool_rows[0].tool_call_id == "call_42"
    assert all("Observations:" not in item.content for item in followup.messages)


@pytest.mark.asyncio
async def test_sdk_resume_approve_shortcut_executes() -> None:
    """SDK resume helper should translate args into resume command."""
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only("file_write"))
    paused = await agent.run(
        AgentRunInput(
            input="Write file.",
            run_id="run_sdk_resume",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": "medium",
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": "/tmp/sdk-resume.txt", "content": "x"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert paused.interrupt is not None
    resumed = await agent.approve(
        run_id="run_sdk_resume", interrupt_id=paused.interrupt.interrupt_id
    )
    assert resumed.status.value == "completed"


@pytest.mark.asyncio
async def test_sdk_run_text_uses_agent_defaults() -> None:
    """SDK run_text should build AgentRunInput from text and defaults."""
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        agent_id="sdk-agent",
        graph_preset="single_react",
    )
    output = await agent.run_text("Hello")
    assert output.status.value == "completed"


@pytest.mark.asyncio
async def test_sdk_create_agent_supports_no_tools_surface() -> None:
    """SDK factory should allow explicit empty tool surface."""
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="No tools.",
            run_id="run_sdk_no_tools",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            args={"query": "blocked"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    assert any(
        item.tool_name == "web_search" and item.status.value == "denied"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
async def test_sdk_create_agent_with_one_custom_tool() -> None:
    """SDK should support registry with exactly one custom tool."""
    registry = ToolRegistry()

    async def _hello(args):
        return {"summary": f"hello {args.get('name', 'world')}"}

    registry.register(
        ToolManifest(
            name="hello_tool",
            description="hello",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
        ),
        _hello,
    )
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        config=RunnerConfig(tool_registry=registry),
        tools=ToolSet.only("hello_tool"),
    )
    output = await agent.run(
        AgentRunInput(
            input="Use hello tool.",
            run_id="run_sdk_custom_tool",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="hello_tool",
                            args={"name": "sdk"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert any(
        item.tool_name == "hello_tool" and item.status.value == "completed"
        for item in output.tool_trace
    )


def test_sdk_default_registry_contains_builtins_and_planning() -> None:
    """SDK helper should expose built-in plus planning tools."""
    registry = build_default_registry()
    names = set(registry.list_names())
    assert "web_search" in names
    assert "planning_state_update" in names


def test_sdk_config_from_env_returns_bootstrap_fields(monkeypatch) -> None:
    """SDK env config helper should expose stable bootstrap keys."""
    monkeypatch.setenv("AGENT_DRIVER_RUN_LIVE_TESTS", "1")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "sqlite")
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "vllm")
    monkeypatch.setenv("AGENT_DRIVER_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("AGENT_DRIVER_MODEL", "test-model")
    monkeypatch.setenv("AGENT_DRIVER_API_KEY", "secret")
    config = sdk_config_from_env()
    assert config.run_live_tests is True
    assert config.runtime_store_kind == "sqlite"
    assert config.provider == "vllm"
    assert config.base_url == "https://example.com/v1"
    assert config.model == "test-model"
    assert config.api_key == "secret"


def test_sdk_create_agent_rejects_unknown_toolset_names() -> None:
    """SDK should fail fast when ToolSet.only includes missing tools."""
    with pytest.raises(ValueError, match="unknown tool names in ToolSet"):
        create_agent(
            provider=FakeProvider(response_text="ok"),
            tools=ToolSet.only("missing_tool"),
        )
