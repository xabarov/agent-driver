"""Epic 044 C: the run output surfaces the per-category context breakdown."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


@pytest.mark.asyncio
async def test_output_metadata_carries_context_breakdown() -> None:
    agent = create_agent(provider=FakeProvider(response_text="done"), tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="what is the weather",
            run_id="run_ctx_breakdown",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    breakdown = output.metadata["context_breakdown"]
    assert set(breakdown["categories"]) >= {
        "system_prompt",
        "tool_definitions",
        "tool_results",
        "scaffolding",
        "conversation",
    }
    # The authoritative total is the same chars//4 estimate the compaction trigger uses.
    assert breakdown["total_tokens"] == breakdown["total_chars"] // 4
    # The user turn lands in the conversation bucket.
    assert breakdown["categories"]["conversation"]["chars"] > 0
