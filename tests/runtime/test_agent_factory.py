"""Tests for high-level SDK create_agent helper with ToolSet wiring."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


@pytest.mark.asyncio
async def test_create_agent_executes_selected_toolset() -> None:
    """Selected tool should remain executable via helper-wired governed executor."""
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("web_search"),
    )
    output = await agent.run(
        AgentRunInput(
            input="Search once.",
            run_id="run_factory_toolset_allow",
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
                                    {
                                        "title": "Agent Driver",
                                        "url": "https://example.com",
                                        "snippet": "runtime",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    assert any(
        item.tool_name == "web_search" and item.status.value == "completed"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
async def test_create_agent_denies_unselected_tool() -> None:
    """Tools removed by ToolSet should be denied even if call is planned."""
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("web_search"),
    )
    output = await agent.run(
        AgentRunInput(
            input="Try read file.",
            run_id="run_factory_toolset_deny",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="read_file",
                            args={"path": "/tmp/nonexistent.txt"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    assert any(
        item.tool_name == "read_file" and item.status.value == "denied"
        for item in output.tool_trace
    )
