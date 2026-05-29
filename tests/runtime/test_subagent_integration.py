"""Runtime integration tests for subagent flag."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    ChatMessage,
    RuntimeEventType,
    ToolCall,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_builtin_tools


class _AgentToolSpawnProvider(FakeProvider):
    """Provider that asks parent to spawn one subagent, then finalizes."""

    def __init__(self) -> None:
        super().__init__(response_text="parent done")
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        usage = UsageSummary(model_provider="fake", model_name="test")
        if self.calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider="fake",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="agent_tool",
                            tool_call_id="agent_tool_call",
                            args={
                                "task": "summarize child evidence",
                                "description": "Summarize child evidence",
                                "idempotency_key": "child-evidence",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        if self.calls == 2:
            return LlmResponse(
                message=ChatMessage(role="assistant", content="child answer"),
                finish_reason=LlmFinishReason.STOP,
                usage=usage,
                provider="fake",
                model="test",
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="parent done"),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider="fake",
            model="test",
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


@pytest.mark.asyncio
async def test_runtime_with_subagents_executes_group_from_agent_tool() -> None:
    """agent_tool envelopes should become native runtime subagent groups."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    event_log = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=_AgentToolSpawnProvider(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            enable_subagents=True,
            max_child_runs=2,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )

    output = await runner.run(
        AgentRunInput(
            input="delegate this",
            run_id="run_agent_tool_sub",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        )
    )

    assert output.answer == "parent done"
    assert output.metadata["subagent_groups"]
    assert output.metadata["subagent_groups"][0]["purpose"] == "agent_tool_spawn"
    assert output.metadata["subagent_runs"]
    assert output.metadata["subagent_runs"][0]["task_id"].startswith("subreq_")
    event_types = [event.type for event in event_log.list_for_run("run_agent_tool_sub")]
    assert RuntimeEventType.SUBAGENT_STARTED in event_types
    assert RuntimeEventType.SUBAGENT_COMPLETED in event_types
