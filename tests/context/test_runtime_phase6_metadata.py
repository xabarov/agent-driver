"""Runtime metadata integration tests for Phase-6 refs."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall, ToolManifest
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry


@pytest.mark.asyncio
async def test_runtime_output_exposes_phase6_artifact_and_digest_refs() -> None:
    """Runtime output metadata should include artifact_refs and digest_refs keys."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_phase6_meta",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert "artifact_refs" in output.metadata
    assert "digest_refs" in output.metadata
    assert isinstance(output.metadata["artifact_refs"], list)
    assert isinstance(output.metadata["digest_refs"], list)
    assert output.metadata["digest_refs"]


@pytest.mark.asyncio
async def test_runtime_stores_oversized_tool_output_as_artifact_ref() -> None:
    """Long tool output should be moved to artifact store with summary preview."""
    registry = ToolRegistry()

    async def _lookup(_args):
        return {"summary": "x" * 900}

    registry.register(
        ToolManifest(
            name="lookup",
            description="Lookup tool",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _lookup,
    )
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_phase6_artifacts",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "mode": "allow_tools",
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(tool_name="lookup", args={}).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    tool_results = output.metadata["tool_results"]
    assert tool_results
    assert tool_results[0]["summary_artifact_ref"]["artifact_id"]
    assert output.metadata["artifact_refs"]


@pytest.mark.asyncio
async def test_runtime_emits_planning_events_and_trim_audit() -> None:
    """Run events should include planning channel and trim metadata."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    output = await runner.run(
        AgentRunInput(
            input="plan this task",
            run_id="run_phase6_planning",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    planning_events = [
        event for event in output.events if event.payload.get("channel") == "planning"
    ]
    assert planning_events
    assert "trim_audit" in output.metadata
    assert "trim_metadata" in output.metadata
    assert "token_pressure" in output.metadata
    assert "microcompaction_audit" in output.metadata
    assert output.memory_projection is not None
    assert output.memory_audit is not None
    assert output.memory_projection.metadata["trim"]


@pytest.mark.asyncio
async def test_runtime_applies_planning_update_tool() -> None:
    """Planning tool output should update planning state in runtime metadata."""
    registry = ToolRegistry()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="update plan",
            run_id="run_phase6_planning_tool",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "mode": "allow_tools",
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="planning_state_update",
                            args={
                                "step": {
                                    "step_id": "step_runtime",
                                    "facts_given": ["update plan"],
                                    "facts_learned": ["tool used"],
                                    "facts_to_lookup": [],
                                    "facts_to_derive": [],
                                    "next_plan": "ship phase6",
                                }
                            },
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    planning_events = [
        event for event in output.events if event.payload.get("channel") == "planning"
    ]
    assert planning_events
    state_events = [
        event for event in planning_events if event.payload.get("todos") is not None
    ]
    assert state_events
