"""Runtime integration tests for subagent flag."""

from __future__ import annotations

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)


@pytest.mark.asyncio
async def test_runtime_without_subagents_keeps_default_flow() -> None:
    """Default runner should not create subagent rows."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_no_sub",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert output.subagent_groups == []
    assert output.subagent_runs == []


@pytest.mark.asyncio
async def test_runtime_with_subagents_executes_group_from_metadata() -> None:
    """Subagent-enabled runtime should produce group/run metadata."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="parent"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(enable_subagents=True, max_child_runs=2),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_with_sub",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_subagent_group": {
                        "group_id": "grp_live",
                        "purpose": "fanout",
                        "join_policy": "wait_all",
                        "merge_mode": "append",
                        "tasks": [
                            {"task_id": "t1", "task": "a", "description": "d1"},
                            {"task_id": "t2", "task": "b", "description": "d2"},
                        ],
                    }
                }
            },
        )
    )
    assert output.metadata.get("subagent_groups")
    assert output.metadata.get("subagent_runs")
