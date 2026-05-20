"""Regression tests for multi-step ReAct loop control."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _ThreeTurnProvider(FakeProvider):
    """Provider that emits two tool rounds, then final answer."""

    def __init__(self, *, repeated_args: bool) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []
        self._repeated_args = repeated_args

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        call_index = len(self.requests)
        if call_index <= 2:
            query = "same-query" if self._repeated_args else f"query-{call_index}"
            result_title = "Result same" if self._repeated_args else f"Result {call_index}"
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="multistep", model_name="test-model"),
                provider="multistep",
                model="test-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            tool_call_id=f"call_{call_index}",
                            args={
                                "query": query,
                                "mock_results": [
                                    {
                                        "title": result_title,
                                        "url": "https://example.com",
                                        "snippet": "ok",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="final answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="multistep", model_name="test-model"),
            provider="multistep",
            model="test-model",
            metadata={},
        )


@pytest.mark.asyncio
async def test_react_loop_allows_second_tool_round_without_forced_none() -> None:
    """Different consecutive tool args should not force tool_choice=none."""
    provider = _ThreeTurnProvider(repeated_args=False)
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="multi step run",
            run_id="run_multistep_react_ok",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=12,
            max_tool_calls=6,
        )
    )
    assert output.answer == "final answer"
    assert len(provider.requests) == 3
    assert provider.requests[1].tool_choice in (None, "auto")
    assert provider.requests[2].tool_choice in (None, "auto")


@pytest.mark.asyncio
async def test_react_loop_forces_none_after_repeated_tool_args() -> None:
    """Two identical consecutive tool calls should trigger forced final-answer mode."""
    provider = _ThreeTurnProvider(repeated_args=True)
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="multi step loop run",
            run_id="run_multistep_react_loop",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=12,
            max_tool_calls=6,
        )
    )
    assert output.answer == "final answer"
    assert len(provider.requests) == 3
    assert provider.requests[2].tool_choice == "none"
