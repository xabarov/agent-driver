"""Opt-in live subagent lane against OpenRouter-compatible provider."""

from __future__ import annotations

import os

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.runtime import FakeSingleStepRunner, InMemoryCheckpointStore, InMemoryEventLog, RunnerConfig
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live

load_local_dotenv_for_live_tests()


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


@pytest.mark.asyncio
async def test_live_subagent_openrouter_lane() -> None:
    """Live subagent lane should execute and expose group lifecycle."""
    if not _live_enabled():
        pytest.skip("live tests disabled")
    base_url = os.getenv("AGENT_DRIVER_OPENAI_BASE_URL") or os.getenv("OPENROUTER_BASE_URL")
    model = os.getenv("AGENT_DRIVER_OPENAI_MODEL") or os.getenv("OPENROUTER_MODEL")
    api_key = os.getenv("AGENT_DRIVER_OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not base_url or not model:
        pytest.skip("OpenRouter live env is not configured")
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter-live-subagent",
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    )
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(enable_subagents=True, max_child_runs=2),
    )
    output = await runner.run(
        AgentRunInput(
            input="Answer in one short line.",
            run_id="run_live_subagent",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_subagent_group": {
                        "group_id": "grp_live_sub",
                        "purpose": "fanout",
                        "join_policy": "wait_all",
                        "merge_mode": "append",
                        "tasks": [
                            {"task_id": "s1", "task": "Summarize one idea", "description": "d1"},
                            {"task_id": "s2", "task": "Summarize another idea", "description": "d2"},
                        ],
                    }
                }
            },
        )
    )
    assert output.metadata.get("subagent_groups")
    assert output.metadata.get("subagent_runs")
