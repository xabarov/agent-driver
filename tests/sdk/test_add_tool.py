"""DX: Agent.add_tool registers a callable tool without a separate ToolSet select."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import CustomToolDefinition, create_agent, tool
from agent_driver.tools import ToolSet


def test_sdk_facade_reexports_tool_primitives() -> None:
    from agent_driver import sdk

    for name in ("tool", "ToolRegistry", "register_custom_function", "CustomToolDefinition"):
        assert hasattr(sdk, name), name


def test_add_tool_from_async_function_registers_and_surfaces() -> None:
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())

    async def lookup_city(city: str) -> dict:
        return {"weather": f"sunny in {city}"}

    manifest = agent.add_tool(lookup_city)
    assert manifest.name == "lookup_city"
    # Surfaces in the LIVE registry the request builder reads.
    assert agent.runner.deps.tool_registry.get("lookup_city") is not None


def test_add_tool_accepts_a_tool_definition() -> None:
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())

    async def ping() -> dict:
        return {"pong": True}

    definition = tool(ping, name="ping")
    assert isinstance(definition, CustomToolDefinition)
    manifest = agent.add_tool(definition)
    assert manifest.name == "ping"
    assert agent.runner.deps.tool_registry.get("ping") is not None


class _CallsAddedToolThenAnswers(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="m"),
                provider="fake",
                model="m",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="lookup_city",
                            tool_call_id="c1",
                            args={"city": "Paris"},
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="done"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="m"),
            provider="fake",
            model="m",
            metadata={},
        )


@pytest.mark.asyncio
async def test_added_tool_actually_executes_in_a_run() -> None:
    provider = _CallsAddedToolThenAnswers()
    agent = create_agent(provider=provider, tools=ToolSet.only())

    seen = {}

    async def lookup_city(city: str) -> dict:
        seen["city"] = city
        return {"weather": f"sunny in {city}"}

    agent.add_tool(lookup_city)  # no ToolSet select needed
    output = await agent.run(
        AgentRunInput(
            input="weather in Paris?",
            run_id="run_add_tool",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert seen.get("city") == "Paris"  # the added tool really ran
    assert output.answer == "done"


@pytest.mark.asyncio
async def test_add_tool_decorator_form() -> None:
    agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())

    @agent.add_tool(name="greet")
    async def greet(name: str) -> dict:
        return {"msg": f"hi {name}"}

    assert agent.runner.deps.tool_registry.get("greet") is not None
