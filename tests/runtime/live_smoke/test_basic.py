"""Optional live smoke tests (split by concern)."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ResumeAction, ResumeCommand, ToolCall, ToolRisk
from tests.support.live_harness import (
    assert_live_interrupt_for_tool,
    build_live_runner,
    notebook_fixture,
    require_live_openrouter_config,
    tool_result,
)


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_tool_call() -> None:
    """Run one live LLM call plus one deterministic built-in tool stage."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Say hello in one short sentence.",
            run_id="run_live_agent_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            args={
                                "query": "agent driver runtime",
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
    envelope = tool_result(output, "web_search")
    assert envelope
    assert envelope["decision"] == "allow"
    assert isinstance(envelope.get("structured_output"), dict)
    tool_trace = output.tool_trace
    assert any(
        item.tool_name == "web_search" and item.status.value == "completed"
        for item in tool_trace
    )


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_web_fetch_extract_mode() -> None:
    """Live lane should execute web_fetch with text extraction mode."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about web fetch verification.",
            run_id="run_live_agent_tool_web_fetch_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_fetch",
                            args={
                                "url": "https://example.com",
                                "extract_mode": "text",
                                "max_chars": 500,
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "web_fetch")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("extract_mode") == "text"
    assert isinstance(structured.get("bytes_loaded"), int)
    assert any(
        item.tool_name == "web_fetch" and item.status.value == "completed"
        for item in output.tool_trace
    )
