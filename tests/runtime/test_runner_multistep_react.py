"""Regression tests for multi-step ReAct loop control."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.runtime.single_agent.tool_stage import _update_zero_result_policy
from agent_driver.runtime.tools import ToolExecutionResult
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


class _ContinuationProvider(FakeProvider):
    """Provider that reports a next step before giving final content."""

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
                        "Шаг структуры завершён. Следующим действием является "
                        "написание черновика статьи."
                    ),
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="continuation", model_name="test-model"),
                provider="continuation",
                model="test-model",
                metadata={},
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="Вот черновик статьи."),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="continuation", model_name="test-model"),
            provider="continuation",
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


@pytest.mark.asyncio
async def test_react_loop_continues_after_progress_only_final_text() -> None:
    provider = _ContinuationProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="напиши статью по плану",
            run_id="run_continuation_nudge",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=8,
            max_tool_calls=2,
        )
    )
    assert output.answer == "Вот черновик статьи."
    assert len(provider.requests) == 2
    assert "Continue with the task" in provider.requests[1].messages[-1].content


def test_upstream_web_search_error_does_not_trigger_zero_result_force_final() -> None:
    """Transient upstream search outages should not disable future tool use."""
    context = SimpleNamespace(metadata={})
    result = ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="web_search", args={"query": "news"}),
                structured_output={
                    "results": [],
                    "parse_status": "upstream_error",
                },
            )
        ]
    )
    _update_zero_result_policy(context, result)
    assert context.metadata["web_search_zero_streak"] == 0
    assert "force_final_answer" not in context.metadata
