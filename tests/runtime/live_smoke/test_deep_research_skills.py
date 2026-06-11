"""Optional live smoke coverage for Deep Research + Skills contracts."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.skills import curated_skills_dir
from tests.support.live_harness import (
    build_live_runner,
    require_live_openrouter_config,
)


@pytest.mark.asyncio
async def test_live_deep_research_skill_and_source_ledger_smoke() -> None:
    """Live lane should preserve skill invocation and source ledger contracts."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly that deep research contracts are wired.",
            run_id="run_live_deep_research_skill_ledger",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "task_contract": {
                        "kind": "research",
                        "requires_research": True,
                        "research_depth": "deep_parallel_research",
                    },
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="skill_view",
                            args={
                                "base_dir": str(curated_skills_dir()),
                                "name": "deep-research-report",
                                "trusted_roots": [str(curated_skills_dir())],
                            },
                        ).model_dump(mode="json"),
                        ToolCall(
                            tool_name="web_fetch",
                            args={
                                "url": "https://example.com",
                                "extract_mode": "text",
                                "max_chars": 500,
                            },
                        ).model_dump(mode="json"),
                    ],
                }
            },
        )
    )

    assert output.status.value == "completed"
    assert output.metadata["source_ledger"]["verified_reads"]
    assert output.metadata["tool_results"]
    assert output.metadata.get("skill_invocations") or output.metadata.get(
        "invoked_skill_refs"
    )
