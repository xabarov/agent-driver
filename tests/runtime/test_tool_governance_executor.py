"""Tool governance executor tests (policy/guardrails/metadata)."""

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
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    evaluate_tool_policy,
)
from tests.runtime.conftest import (
    BlockingToolArgsGuardrails,
    BlockingToolInputGuardrails,
    SanitizeToolResultGuardrails,
    llm_request_with_planned_calls,
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
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup", args={"query": "abcdef"})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert len(result.traces) == 1
    assert result.traces[0].truncated


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
    executor = GovernedToolExecutor(
        registry=registry, guardrails=BlockingToolArgsGuardrails()
    )
    run_input = AgentRunInput(
        input="hello",
        run_id="run_guard_block",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
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
        registry=registry, guardrails=BlockingToolInputGuardrails()
    )
    run_input = AgentRunInput(
        input="hello",
        run_id="run_guard_input_block",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
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
    executor = GovernedToolExecutor(
        registry=registry, guardrails=SanitizeToolResultGuardrails()
    )
    run_input = AgentRunInput(
        input="hello",
        run_id="run_guard_sanitize",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
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
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="lookup_tool", args={"q": "x"})]
        )
    )
    result = await executor.execute(run_input, response)
    meta = result.envelopes[0].metadata
    assert meta["agent_profile"] == "react_text"
    assert meta["prompt_template_id"] == "react.default"
    assert meta["prompt_template_version"] == 2
