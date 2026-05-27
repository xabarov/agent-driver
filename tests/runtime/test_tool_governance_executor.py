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


@pytest.mark.asyncio
async def test_governed_executor_unknown_tool_returns_fuzzy_match_suggestion() -> None:
    """Phase 13 H29.3 — when the model calls a tool that's close to a
    registered name (typo), the executor's block envelope should carry
    the fuzzy-match suggestion in the ``reason`` field so the next LLM
    turn can self-correct."""
    registry = ToolRegistry()

    async def _screenshot(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(
            name="screenshot_tool",
            description="Take a screenshot",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _screenshot,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hello",
        run_id="run_unknown_tool_fuzzy",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            # Typo: "scrennshot_tool" — one transposed letter from
            # the registered "screenshot_tool".
            planned=[ToolCall(tool_name="scrennshot_tool", args={})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.envelopes, "expected at least one envelope for the blocked call"
    envelope = result.envelopes[0]
    assert envelope.error is not None
    assert envelope.error.code == "tool_not_registered"
    reason = envelope.error.message
    assert "scrennshot_tool" in reason  # quoted name surfaced
    assert "screenshot_tool" in reason  # fuzzy match surfaced
    assert "Available tools:" in reason


@pytest.mark.asyncio
async def test_governed_executor_unknown_tool_without_fuzzy_match() -> None:
    """When the misspelled name doesn't pass the fuzzy-match cutoff, the
    feedback still includes the catalog listing — model can pick a tool
    from there if it had a fully-unrelated hallucination."""
    registry = ToolRegistry()

    async def _alpha(_args):
        return {}

    registry.register(
        ToolManifest(
            name="alpha",
            description="Alpha",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _alpha,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hello",
        run_id="run_unknown_tool_unrelated",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="zzz_unrelated_name", args={})]
        )
    )
    result = await executor.execute(run_input, response)
    envelope = result.envelopes[0]
    assert envelope.error.code == "tool_not_registered"
    reason = envelope.error.message
    assert "zzz_unrelated_name" in reason
    assert "Did you mean:" not in reason  # no candidate above cutoff
    assert "Available tools:" in reason
    assert "alpha" in reason


@pytest.mark.asyncio
async def test_governed_executor_bounds_structured_output_lists() -> None:
    """Executor should cap oversized structured outputs and expose omitted_count."""
    registry = ToolRegistry()

    async def _lookup(_args):
        return {
            "summary": "ok",
            "results": [f"item_{idx}" for idx in range(100)],
        }

    registry.register(
        ToolManifest(
            name="lookup",
            description="Lookup",
            output_char_budget=200,
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
        ),
        _lookup,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hello",
        run_id="run_tools_bound",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(planned=[ToolCall(tool_name="lookup", args={})])
    )
    result = await executor.execute(run_input, response)
    payload = result.envelopes[0].structured_output
    assert isinstance(payload, dict)
    assert payload["truncated"] is True
    assert payload["limit"] == "output_char_budget"
    assert payload["omitted_count"] > 0


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


@pytest.mark.asyncio
async def test_governed_executor_converts_handler_exception_to_denied_trace() -> None:
    """Tool handler exceptions should not crash run; return denied envelope."""
    registry = ToolRegistry()

    async def _explode(_args):
        raise ValueError("boom")

    registry.register(
        ToolManifest(name="explode", description="Explode"),
        _explode,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hello",
        run_id="run_handler_error",
        agent_id="agent",
        graph_preset="single_react",
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="explode", args={"x": 1})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.traces[0].status.value == "denied"
    assert result.traces[0].error_code == "tool_handler_error"
    assert result.envelopes[0].error is not None
    assert result.envelopes[0].error.code == "tool_handler_error"
