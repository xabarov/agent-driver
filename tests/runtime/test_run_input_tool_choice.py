"""Runtime tests for the public ``AgentRunInput.tool_choice`` seam.

The provider-level ``tool_choice`` field has existed on ``LlmRequest`` and
through the OpenAI / Anthropic adapters for a while, but until now there
was no way for a *caller* to set it — the inner-loop's
``context.metadata["tool_choice_override"]`` was the only writer, and it
only ever switched the model to ``"none"`` after the loop detected a
runaway tool sequence.

These tests prove the new public seam:
  * a caller can set ``tool_choice`` on ``AgentRunInput`` and the very
    first LLM request the provider sees carries that value;
  * the inner-loop override still wins when it fires (so the runtime's
    safety mechanisms can't be silently disabled by a caller).

The motivating use case (see ``docs/runtime/tool_choice.md``): a host
detects that a turn promised a chart but never invoked ``chart_vegalite``,
re-invokes the agent with
``tool_choice={"type": "tool", "name": "chart_vegalite"}``, and the
provider GUARANTEES a chart tool call instead of relying on prompt
discipline.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _CapturingProvider(FakeProvider):
    """Records every ``LlmRequest`` it sees, returns final-answer once."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="test"),
            provider="fake",
            model="test",
            metadata={},
        )


class _ForcedToolProvider(FakeProvider):
    """Like ``_CapturingProvider`` but emits two identical tool calls so the
    inner-loop's ``force_none`` safety mechanism kicks in on the third LLM
    request. Used to prove the override wins over the caller's setting."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        call_index = len(self.requests)
        if call_index <= 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="test"),
                provider="fake",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            tool_call_id=f"call_{call_index}",
                            args={
                                "query": "same",
                                "mock_results": [
                                    {
                                        "title": "R",
                                        "url": "https://example.com",
                                        "snippet": "x",
                                    }
                                ],
                            },
                        ).model_dump(mode="json"),
                    ],
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="final"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="test"),
            provider="fake",
            model="test",
            metadata={},
        )


@pytest.mark.asyncio
async def test_run_input_tool_choice_string_required_reaches_provider() -> None:
    """``tool_choice="required"`` set on ``AgentRunInput`` must appear on the
    first ``LlmRequest`` the provider receives."""
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_tc_required",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
            tool_choice="required",
        )
    )
    assert provider.requests, "provider never received a request"
    assert provider.requests[0].tool_choice == "required"


@pytest.mark.asyncio
async def test_run_input_tool_choice_specific_tool_reaches_provider() -> None:
    """``{"type":"tool","name":"X"}`` round-trips through the runtime to
    the provider unchanged — the dict shape is what closes the
    chart-promise loophole."""
    forced = {"type": "tool", "name": "web_search"}
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_tc_specific",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
            tool_choice=forced,
        )
    )
    assert provider.requests[0].tool_choice == forced


@pytest.mark.asyncio
async def test_run_input_tool_choice_none_preserves_legacy_default() -> None:
    """Unset ``tool_choice`` leaves the runtime's default behaviour intact
    — the provider sees ``None`` (its adapters then apply ``"auto"`` for
    OpenAI-compatible backends)."""
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_tc_default",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
        )
    )
    assert provider.requests[0].tool_choice is None


@pytest.mark.asyncio
async def test_inner_loop_override_wins_over_caller_tool_choice() -> None:
    """The inner-loop ``"none"`` override (triggered by repeated identical
    tool args, see ``_update_zero_result_policy``) must override a
    caller-supplied ``tool_choice``. This protects the runaway-loop safety
    rail from being silently bypassed by callers."""
    provider = _ForcedToolProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="run_tc_override_wins",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=8,
            max_tool_calls=4,
            tool_choice={"type": "tool", "name": "web_search"},
        )
    )
    # First two calls carry the caller-supplied forced tool ...
    assert provider.requests[0].tool_choice == {
        "type": "tool",
        "name": "web_search",
    }
    assert provider.requests[1].tool_choice == {
        "type": "tool",
        "name": "web_search",
    }
    # ... but on the third (after two identical args), the inner-loop
    # safety rail forces "none" so the model has to finalize.
    assert provider.requests[2].tool_choice == "none"


@pytest.mark.asyncio
async def test_chat_mode_initial_tool_choice_only_applies_to_first_llm_call() -> None:
    """Chat hosts use AgentRunInput.tool_choice as an initial nudge.

    After the first tool result, the model should return to auto choice unless
    a runtime guard sets an explicit override.
    """
    provider = _ForcedToolProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("web_search"))

    await agent.run(
        AgentRunInput(
            input="найди один источник",
            run_id="run_tc_chat_initial_only",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=3,
            tool_choice={"type": "tool", "name": "web_search"},
            app_metadata={"chat_mode": True},
        )
    )

    assert provider.requests[0].tool_choice == {
        "type": "tool",
        "name": "web_search",
    }
    assert provider.requests[1].tool_choice is None
