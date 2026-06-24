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
)
from agent_driver.tools.registry import ToolRegistry


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


class _RaisingCodeExecutor:
    """Executor whose ``execute`` raises a non-CodeExecutionError exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def execute(self, **_kwargs):  # noqa: ANN003 - test stub
        raise self._exc


@pytest.mark.asyncio
async def test_code_agent_profile_maps_runtime_error_to_failed_trace() -> None:
    """A non-interpreter exception from the executor is a tool failure, not a crash."""
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ignored"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            code_executor=_RaisingCodeExecutor(KeyError("missing_col")),
        ),
    )
    # The run must not propagate the KeyError; it records a FAILED code_action
    # trace carrying the redacted runtime-error summary. A raising executor
    # never reaches final_answer, so bound the loop with max_steps — otherwise
    # the deterministic step loop runs forever (limits default to None).
    output = await runner.run(
        AgentRunInput(
            input="do arithmetic",
            run_id="run_code_profile_runtime_error",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "final_answer(2 + 2)"}},
            max_steps=2,
        )
    )
    results = output.metadata["tool_results"]
    assert results
    summary = results[0]["summary"]
    assert summary == "code_runtime_error: KeyError"
    # Redacted to the exception type — the raw message must not leak.
    assert "missing_col" not in summary


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


@pytest.mark.asyncio
async def test_code_agent_does_not_interrupt_for_unused_side_effect_tool() -> None:
    """Side-effect tool in registry should not interrupt unless called by code."""
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
        config=RunnerConfig(
            tool_registry=registry, code_executor=FakeRestrictedCodeExecutor()
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="safe action",
            run_id="run_code_profile_unused_side_effect",
            agent_id="agent",
            graph_preset="single_react",
            agent_profile=AgentProfile.CODE_AGENT,
            tool_policy={"metadata": {"code_action": "final_answer('ok')"}},
        )
    )
    assert output.status.value == "completed"
    assert output.interrupt is None
