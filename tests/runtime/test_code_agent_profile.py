"""Runtime integration tests for CodeAgent profile."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import FakeRestrictedCodeExecutor
from agent_driver.contracts import (
    AgentProfile,
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    ToolRegistry,
)


@pytest.mark.asyncio
async def test_code_agent_profile_executes_code_action() -> None:
    """Code-agent profile should execute action and emit tool_results."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(
            response_text="ignored",
        ),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(code_executor=FakeRestrictedCodeExecutor()),
    )
    output = await runner.run(
        AgentRunInput(
            input="do arithmetic",
            run_id="run_code_profile_ok",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "final_answer(2 + 2)"}},
        )
    )
    assert output.status.value == "completed"
    assert output.metadata["tool_results"]
    assert output.metadata["tool_results"][0]["summary"] == "4"
    assert "tool_docs" in output.metadata["tool_results"][0]["metadata"]


@pytest.mark.asyncio
async def test_code_agent_profile_parses_fenced_code_action_from_response_text() -> (
    None
):
    """Code-agent should parse fenced python action from model message content."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(
            response_text="```python\nprint('hello')\nfinal_answer(6)\n```"
        ),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(code_executor=FakeRestrictedCodeExecutor()),
    )
    output = await runner.run(
        AgentRunInput(
            input="do arithmetic",
            run_id="run_code_profile_fenced",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
        )
    )
    assert output.status.value == "completed"
    assert output.metadata["tool_results"][0]["summary"] == "6"
    observations = output.metadata.get("observations", [])
    assert any(
        item.get("provenance", {}).get("source") == "tool_stdout"
        for item in observations
    )


@pytest.mark.asyncio
async def test_code_agent_profile_interrupts_side_effect_tool() -> None:
    """Side-effecting tools in code profile should request approval."""
    registry = ToolRegistry()

    async def _danger(_args):
        return {"summary": "danger"}

    registry.register(
        ToolManifest(
            name="danger",
            description="Danger",
            risk=ToolRisk.HIGH,
            side_effect=SideEffectClass.EXTERNAL_ACTION,
            approval_mode=ApprovalMode.ALWAYS,
        ),
        _danger,
    )
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ignored"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_registry=registry),
    )
    output = await runner.run(
        AgentRunInput(
            input="run danger",
            run_id="run_code_profile_interrupt",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "danger()"}},
        )
    )
    assert output.status.value == "paused"
    assert output.interrupt is not None
