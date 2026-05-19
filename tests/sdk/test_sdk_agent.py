"""Tests for app-facing SDK Agent facade."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import AgentRunInput, RuntimeEventType, ToolCall, ToolManifest
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmStreamEvent
from agent_driver.runtime import RunnerConfig
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
    monkeypatch.setenv("AGENT_DRIVER_OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("AGENT_DRIVER_OPENAI_MODEL", "test-model")
    config = sdk_config_from_env()
    assert config.run_live_tests is True
    assert config.runtime_store_kind == "sqlite"
    assert config.openai_base_url == "https://example.com/v1"
    assert config.openai_model == "test-model"
