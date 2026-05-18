"""Tool governance integration tests for runtime executor seam."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentProfile,
    AgentRunInput,
    ApprovalMode,
    GuardrailDecision,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    GovernedToolExecutor,
    GuardrailPipeline,
    GuardrailResult,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    ToolRegistry,
    evaluate_tool_policy,
    wrap_governed_executor,
)


class _BlockingGuardrails(GuardrailPipeline):
    async def on_tool_args(self, payload: dict[str, object]) -> GuardrailResult:
        if payload.get("args", {}).get("blocked"):
            return GuardrailResult(
                decision=GuardrailDecision.BLOCK,
                reason="args blocked by guardrail",
            )
        return await super().on_tool_args(payload)


class _InputBlockingGuardrails(GuardrailPipeline):
    async def on_input(self, payload: dict[str, object]) -> GuardrailResult:
        if payload.get("tool_name") == "lookup":
            return GuardrailResult(
                decision=GuardrailDecision.BLOCK,
                reason="input blocked by guardrail",
            )
        return await super().on_input(payload)


class _SanitizeGuardrails(GuardrailPipeline):
    async def on_tool_result(self, payload: dict[str, object]) -> GuardrailResult:
        _ = payload
        return GuardrailResult(
            decision=GuardrailDecision.SANITIZE,
            reason="sanitize marker",
        )


@pytest.mark.asyncio
async def test_governed_executor_completes_tool_and_truncates() -> None:
    """Executor should run registered tool and enforce result budget."""
    registry = ToolRegistry()

    async def _lookup(args):
        return {"summary": f"value:{args['query']}"}

    registry.register(
        ToolManifest(
            name="lookup",
            description="Lookup",
            output_char_budget=5,
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _lookup,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hello",
        run_id="run_tools_ok",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(
        response_text="ok",
    )
    response = await provider.complete(
        _request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup", args={"query": "abcdef"})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert len(result.traces) == 1
    assert result.traces[0].truncated


def _request_with_planned_calls(planned: list[ToolCall]) -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role="user", content="hello")],
        metadata={
            "planned_tool_calls": [call.model_dump(mode="json") for call in planned]
        },
    )


@pytest.mark.asyncio
async def test_runner_interrupts_for_high_risk_policy() -> None:
    """Runner should return paused output when policy requests interrupt."""
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
    governed = GovernedToolExecutor(registry=registry)
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=wrap_governed_executor(governed)),
    )
    output = await runner.run(
        AgentRunInput(
            input="hello",
            run_id="run_interrupt_1",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(
                mode=ToolPolicyMode.ALLOW_TOOLS,
                approval_required_for_risk=ToolRisk.HIGH,
                metadata={
                    "planned_tool_calls": [
                        {"tool_name": "danger", "args": {"target": "x"}}
                    ]
                },
            ),
        )
    )
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert any(event.type.value == "interrupt_requested" for event in output.events)


def test_policy_denies_explicit_denied_tool() -> None:
    """Policy engine should deny tool present in denied list."""
    call = ToolCall(tool_name="danger")
    manifest = ToolManifest(name="danger", description="Danger")
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            denied_tools=["danger"],
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "deny"


@pytest.mark.asyncio
async def test_governed_executor_guardrail_blocks_args() -> None:
    """Guardrail should block tool execution when args are unsafe."""
    registry = ToolRegistry()

    async def _lookup(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(name="lookup", description="Lookup"),
        _lookup,
    )
    executor = GovernedToolExecutor(registry=registry, guardrails=_BlockingGuardrails())
    run_input = AgentRunInput(
        input="hello",
        run_id="run_guard_block",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        _request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup", args={"blocked": True})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.traces[0].status.value == "denied"
    assert result.envelopes[0].error is not None


@pytest.mark.asyncio
async def test_governed_executor_guardrail_blocks_input() -> None:
    """Input guardrail hook should block tool before args/handler stages."""
    registry = ToolRegistry()

    async def _lookup(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(name="lookup", description="Lookup"),
        _lookup,
    )
    executor = GovernedToolExecutor(
        registry=registry, guardrails=_InputBlockingGuardrails()
    )
    run_input = AgentRunInput(
        input="hello",
        run_id="run_guard_input_block",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        _request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup", args={"q": "x"})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.traces[0].status.value == "denied"
    assert result.envelopes[0].metadata["guardrail_stage"] == "input"


@pytest.mark.asyncio
async def test_governed_executor_marks_sanitize_decision() -> None:
    """Sanitize decision should be preserved in result envelope."""
    registry = ToolRegistry()

    async def _lookup(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(name="lookup", description="Lookup"),
        _lookup,
    )
    executor = GovernedToolExecutor(registry=registry, guardrails=_SanitizeGuardrails())
    run_input = AgentRunInput(
        input="hello",
        run_id="run_guard_sanitize",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        _request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup", args={"q": "x"})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.envelopes[0].guardrail_decision == GuardrailDecision.SANITIZE


@pytest.mark.asyncio
async def test_governed_executor_includes_profile_and_prompt_metadata() -> None:
    """Tool envelopes should carry run profile/template metadata."""
    registry = ToolRegistry()

    async def _lookup(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(name="lookup_tool", description="Lookup"),
        _lookup,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hello",
        run_id="run_meta_1",
        agent_id="agent",
        graph_preset="single_react",
        agent_profile=AgentProfile.REACT_TEXT,
        prompt_template_id="react.default",
        prompt_template_version=2,
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        _request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup_tool", args={"q": "x"})]
        )
    )
    result = await executor.execute(run_input, response)
    meta = result.envelopes[0].metadata
    assert meta["agent_profile"] == "react_text"
    assert meta["prompt_template_id"] == "react.default"
    assert meta["prompt_template_version"] == 2
