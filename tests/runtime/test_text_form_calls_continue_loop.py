"""Regression: text-form tool calls with finish_reason STOP should continue ReAct loop."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _TextFormTwoTurnProvider(FakeProvider):
    """Emits one text-form tool round (STOP), then final answer."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(
                    role="assistant",
                    content=(
                        "Searching.\n"
                        '<tool_call>{"name":"glob_search","arguments":{"pattern":"*.md"}}</tool_call>'
                    ),
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="textform", model_name="test-model"),
                provider="textform",
                model="test-model",
                metadata={},
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="found markdown files"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="textform", model_name="test-model"),
            provider="textform",
            model="test-model",
            metadata={},
        )


@pytest.mark.asyncio
async def test_text_form_tool_calls_trigger_second_llm_round() -> None:
    """STOP + text-form tool_call content should execute tool then call LLM again."""
    provider = _TextFormTwoTurnProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("glob_search"))
    output = await agent.run(
        AgentRunInput(
            input="find markdown files",
            run_id="run_text_form_loop",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=12,
            max_tool_calls=6,
        )
    )
    assert output.answer == "found markdown files"
    assert len(provider.requests) == 2
    tool_names = [row.tool_name for row in output.tool_trace]
    assert "glob_search" in tool_names
