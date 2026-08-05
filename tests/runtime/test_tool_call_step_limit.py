"""Per-model-step tool-call limit contracts."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, RuntimeEventType, ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _BatchProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) > 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content="done"),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="fake", model_name="batch"),
                provider="fake",
                model="batch",
            )
        calls = [
            ToolCall(
                tool_name="web_search",
                tool_call_id=f"call_{index}",
                args={
                    "query": f"query-{index}",
                    "mock_results": [
                        {
                            "title": f"Result {index}",
                            "url": f"https://example.com/{index}",
                            "snippet": "ok",
                        }
                    ],
                },
            ).model_dump(mode="json")
            for index in range(3)
        ]
        return LlmResponse(
            message=ChatMessage(role="assistant", content=""),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            usage=UsageSummary(model_provider="fake", model_name="batch"),
            provider="fake",
            model="batch",
            metadata={"planned_tool_calls": calls},
        )


@pytest.mark.asyncio
async def test_single_call_step_limit_reaches_provider_and_clamps_runtime() -> None:
    provider = _BatchProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))

    output = await agent.run(
        AgentRunInput(
            input="use evidence sequentially",
            run_id="run_single_call_step_limit",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=6,
            max_tool_calls=6,
            max_tool_calls_per_step=1,
        )
    )

    assert output.answer == "done"
    assert output.metadata["tool_calls"] == 1
    assert provider.requests[0].parallel_tool_calls is False
    second_messages = provider.requests[1].messages
    assistant_calls = [
        message.metadata["tool_calls"]
        for message in second_messages
        if message.role.value == "assistant" and "tool_calls" in message.metadata
    ]
    assert len(assistant_calls) == 1
    assert [call["id"] for call in assistant_calls[0]] == ["call_0"]
    assert [
        message.tool_call_id
        for message in second_messages
        if message.role.value == "tool"
    ] == ["call_0"]
    signals = [
        event.payload
        for event in output.events
        if event.type == RuntimeEventType.WARNING
        and event.payload.get("signal_id") == "planned_tool_call_step_limit_applied"
    ]
    assert signals == [
        {
            "signal_id": "planned_tool_call_step_limit_applied",
            "severity": "info",
            "limit": 1,
            "planned_count": 3,
            "accepted_count": 1,
            "suppressed_count": 2,
            "suppressed_tools": ["web_search", "web_search"],
        }
    ]


@pytest.mark.asyncio
async def test_unset_step_limit_preserves_parallel_batch_behavior() -> None:
    provider = _BatchProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))

    output = await agent.run(
        AgentRunInput(
            input="use the batch",
            run_id="run_default_batch",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=6,
            max_tool_calls=6,
        )
    )

    assert output.answer == "done"
    assert output.metadata["tool_calls"] == 3
    assert provider.requests[0].parallel_tool_calls is None


def test_step_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="run limit must be > 0"):
        AgentRunInput(
            input="invalid",
            agent_id="agent",
            graph_preset="single_react",
            max_tool_calls_per_step=0,
        )


@pytest.mark.asyncio
async def test_runner_default_applies_when_run_does_not_override_limit() -> None:
    provider = _BatchProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("web_search"),
        config=RunnerConfig(default_max_tool_calls_per_step=1),
    )

    output = await agent.run(
        AgentRunInput(
            input="use the host default",
            run_id="run_default_single_step",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=6,
            max_tool_calls=6,
        )
    )

    assert output.metadata["tool_calls"] == 1
    assert provider.requests[0].parallel_tool_calls is False


def test_runner_default_step_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="default_max_tool_calls_per_step must be > 0"):
        RunnerConfig(default_max_tool_calls_per_step=0)
