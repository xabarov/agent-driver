"""Regression tests for multi-step ReAct loop control."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall, ToolPolicyInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.tool_stage import _update_zero_result_policy
from agent_driver.runtime.tools import ToolExecutionResult
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
            result_title = (
                "Result same" if self._repeated_args else f"Result {call_index}"
            )
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
                usage=UsageSummary(
                    model_provider="continuation", model_name="test-model"
                ),
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


class _RussianProgressProvider(FakeProvider):
    """Provider that stops after a Russian progress update from the chat demo."""

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
                        "Информация о гитарах Fender собрана: изучена история "
                        "компании, основные модели и их особенности. Теперь работаю "
                        "над следующим шагом — структурирую найденную информацию "
                        "для дальнейшего написания реферата."
                    ),
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(
                    model_provider="continuation", model_name="test-model"
                ),
                provider="continuation",
                model="test-model",
                metadata={},
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="Вот готовый реферат."),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="continuation", model_name="test-model"),
            provider="continuation",
            model="test-model",
            metadata={},
        )


class _TextFormToolCallProvider(FakeProvider):
    """Provider that incorrectly prints a tool call instead of using native tools."""

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
                        '{"name": "todo_update", "arguments": '
                        '{"todo_id": "research", "status": "in_progress"}} '
                        "</tool_call>"
                    ),
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(
                    model_provider="continuation", model_name="test-model"
                ),
                provider="continuation",
                model="test-model",
                metadata={},
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="Вот готовый ответ."),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="continuation", model_name="test-model"),
            provider="continuation",
            model="test-model",
            metadata={},
        )


class _DeliverablePlanOnlyProvider(FakeProvider):
    """Provider that tries to keep planning after a deliverable request."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(
                    model_provider="deliverable", model_name="test-model"
                ),
                provider="deliverable",
                model="test-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="todo_write",
                            tool_call_id="todo_deliverable",
                            args={
                                "todos": [
                                    {
                                        "id": "draft",
                                        "content": "Написать черновик",
                                        "status": "in_progress",
                                    }
                                ]
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="Вот готовый черновик."),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="deliverable", model_name="test-model"),
            provider="deliverable",
            model="test-model",
            metadata={},
        )


class _ForceFinalProgressProvider(FakeProvider):
    """Provider that reports another step even after force-final is active."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(
                    model_provider="deliverable", model_name="test-model"
                ),
                provider="deliverable",
                model="test-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            tool_call_id="web_deliverable",
                            args={
                                "query": "Fender history",
                                "mock_results": [
                                    {
                                        "title": "Fender",
                                        "url": "https://example.com/fender",
                                        "snippet": "history",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if len(self.requests) == 2:
            return LlmResponse(
                message=ChatMessage(
                    role="assistant",
                    content=(
                        "Собрана информация о Fender. Следующим шагом будет "
                        "написание короткого реферата."
                    ),
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(
                    model_provider="deliverable", model_name="test-model"
                ),
                provider="deliverable",
                model="test-model",
                metadata={},
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="Вот короткий реферат."),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="deliverable", model_name="test-model"),
            provider="deliverable",
            model="test-model",
            metadata={},
        )


class _ResearchRequirementProvider(FakeProvider):
    """Provider that first tries to answer a research request without tools."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(
                    role="assistant",
                    content="Короткий реферат без фактического поиска.",
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="research", model_name="test-model"),
                provider="research",
                model="test-model",
                metadata={},
            )
        if len(self.requests) == 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="research", model_name="test-model"),
                provider="research",
                model="test-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            tool_call_id="web_required",
                            args={
                                "query": "Fender history",
                                "mock_results": [
                                    {
                                        "title": "Fender",
                                        "url": "https://example.com/fender",
                                        "snippet": "history",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="Финал после поиска."),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="research", model_name="test-model"),
            provider="research",
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


@pytest.mark.asyncio
async def test_react_loop_continues_after_russian_progress_only_text() -> None:
    provider = _RussianProgressProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="напиши реферат по истории Fender",
            run_id="run_russian_continuation_nudge",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=8,
            max_tool_calls=2,
        )
    )
    assert output.answer == "Вот готовый реферат."
    assert len(provider.requests) == 2
    assert "Continue with the task" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_react_loop_recovers_from_text_form_tool_call_answer() -> None:
    provider = _TextFormToolCallProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="составь план и ответ",
            run_id="run_text_form_tool_call_nudge",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=8,
            max_tool_calls=2,
        )
    )
    assert output.answer == "Вот готовый ответ."
    assert len(provider.requests) == 2
    assert "printed a tool call as text" in provider.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_deliverable_request_forces_final_after_plan_only_tool() -> None:
    """A deliverable turn should not continue planning after todo_write."""
    provider = _DeliverablePlanOnlyProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("todo_write"))
    output = await agent.run(
        AgentRunInput(
            input="напиши черновик по плану, не план",
            run_id="run_deliverable_plan_only",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                metadata={"deliverable_request": {"enabled": True}}
            ),
            max_steps=8,
            max_tool_calls=4,
        )
    )
    assert output.answer == "Вот готовый черновик."
    assert len(provider.requests) == 2
    assert provider.requests[1].tool_choice == "none"
    assert (
        "Produce the requested deliverable" in provider.requests[1].messages[-1].content
    )


@pytest.mark.asyncio
async def test_force_final_still_continues_after_progress_only_text() -> None:
    provider = _ForceFinalProgressProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="напиши короткий реферат по Fender",
            run_id="run_force_final_progress_nudge",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                metadata={"deliverable_request": {"enabled": True}}
            ),
            max_steps=10,
            max_tool_calls=4,
        )
    )
    assert output.answer == "Вот короткий реферат."
    assert len(provider.requests) == 3
    assert provider.requests[1].tool_choice == "none"
    assert provider.requests[2].tool_choice == "none"


@pytest.mark.asyncio
async def test_research_contract_continues_when_final_answer_has_no_web_results() -> (
    None
):
    provider = _ResearchRequirementProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="составь план поиска в интернете и написания реферата",
            run_id="run_research_requirement_missing",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                metadata={
                    "task_contract": {
                        "kind": "deliverable",
                        "requires_research": True,
                        "goal": "составь план поиска в интернете и написания реферата",
                    }
                }
            ),
            max_steps=10,
            max_tool_calls=4,
        )
    )
    assert output.answer == "Финал после поиска."
    assert len(provider.requests) == 3
    assert "no web/data tool results" in provider.requests[1].messages[-1].content


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
