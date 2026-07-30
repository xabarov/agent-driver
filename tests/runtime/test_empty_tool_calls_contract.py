"""Epic 042 B: finish_reason=tool_calls + empty array → bounded re-prompt, not finalize."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _EmptyToolCallsThenAnswer(FakeProvider):
    """Emits finish_reason=tool_calls with an EMPTY tool_calls array N times, then answers."""

    def __init__(self, *, empty_rounds: int) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []
        self._empty_rounds = empty_rounds

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) <= self._empty_rounds:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),  # no usable answer
                finish_reason=LlmFinishReason.TOOL_CALLS,  # says "tool" ...
                usage=UsageSummary(model_provider="fake", model_name="m"),
                provider="fake",
                model="m",
                metadata={"planned_tool_calls": []},  # ... but ships nothing
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="the real final answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="m"),
            provider="fake",
            model="m",
            metadata={},
        )


@pytest.mark.asyncio
async def test_empty_tool_calls_reprompts_beyond_the_015_budget() -> None:
    # THREE empty-tool-calls rounds: the epic-015 empty-answer retry alone gives up
    # after 1, so recovering here proves the 042-B contract re-prompt extended the
    # budget for the finish_reason=tool_calls signal specifically.
    provider = _EmptyToolCallsThenAnswer(empty_rounds=3)
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="review the PR",
            run_id="run_empty_tc",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=12,
            max_tool_calls=6,
        )
    )
    assert output.answer == "the real final answer"
    assert len(provider.requests) == 4  # 3 empty rounds re-prompted, then the answer


@pytest.mark.asyncio
async def test_empty_tool_calls_reprompt_is_bounded() -> None:
    # A provider that NEVER ships a call must not spin: bounded to 3 re-prompts,
    # then the run finalizes instead of looping forever.
    provider = _EmptyToolCallsThenAnswer(empty_rounds=99)
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    output = await agent.run(
        AgentRunInput(
            input="review the PR",
            run_id="run_empty_tc_bounded",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=20,
            max_tool_calls=6,
        )
    )
    # Bounded: the empty-tool-calls re-prompt caps at 3 (other ladders may add a
    # couple), so the run terminates well below max_steps=20 instead of spinning.
    assert len(provider.requests) <= 8
    assert output is not None
