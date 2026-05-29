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


def test_policy_force_planning_denies_write_without_approved_plan() -> None:
    """Force planning should block side-effecting tools until a plan is approved."""
    call = ToolCall(tool_name="file_write")
    manifest = ToolManifest(
        name="file_write",
        description="Write file",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={"force_planning": {"enabled": True}},
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "deny"
    assert "approved plan" in outcome.reason
    assert outcome.metadata["force_planning"]["required"] is True


def test_policy_force_planning_allows_planning_tool_without_approved_plan() -> None:
    """Planning tools must remain available so the model can request approval."""
    call = ToolCall(tool_name="exit_plan_mode_v2")
    manifest = ToolManifest(
        name="exit_plan_mode_v2",
        description="Exit plan mode",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.NONE,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={"force_planning": {"enabled": True}},
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "allow"


def test_policy_force_planning_allows_write_with_approved_plan() -> None:
    """An approved plan marker should unblock the gated tool surface."""
    call = ToolCall(tool_name="file_write")
    manifest = ToolManifest(
        name="file_write",
        description="Write file",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={
                "force_planning": {
                    "enabled": True,
                    "approved_plan_id": "plan_123",
                }
            },
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "allow"


def test_policy_force_planning_prompt_only_does_not_gate() -> None:
    """prompt_only mode should leave execution ungated for voluntary planning."""
    call = ToolCall(tool_name="file_write")
    manifest = ToolManifest(
        name="file_write",
        description="Write file",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={"force_planning": {"enabled": True, "mode": "prompt_only"}},
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "allow"


def test_policy_force_planning_explicit_disabled_wins_over_mode() -> None:
    """Explicit enabled=false should disable the gate even when mode is present."""
    call = ToolCall(tool_name="file_write")
    manifest = ToolManifest(
        name="file_write",
        description="Write file",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={
                "force_planning": {
                    "enabled": False,
                    "mode": "required_for_writes",
                }
            },
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "allow"


def test_policy_force_planning_required_for_risky_tools_blocks_read_only() -> None:
    """Risk mode should gate high-risk tools even without write side effects."""
    call = ToolCall(tool_name="prod_query")
    manifest = ToolManifest(
        name="prod_query",
        description="Query production data",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.READ_ONLY,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={"force_planning": {"mode": "required_for_risky_tools"}},
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=0,
    )
    assert outcome.decision.value == "deny"
    assert outcome.metadata["force_planning"]["mode"] == "required_for_risky_tools"


def test_policy_force_planning_always_for_multistep_blocks_after_threshold() -> None:
    """Multistep mode should gate non-exempt tools once the step threshold is hit."""
    call = ToolCall(tool_name="lookup")
    manifest = ToolManifest(
        name="lookup",
        description="Lookup",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
    )
    outcome = evaluate_tool_policy(
        policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={
                "force_planning": {
                    "mode": "always_for_multistep",
                    "step_threshold": 2,
                }
            },
        ),
        manifest=manifest,
        call=call,
        current_tool_calls=1,
    )
    assert outcome.decision.value == "deny"
    assert outcome.metadata["force_planning"]["mode"] == "always_for_multistep"


@pytest.mark.asyncio
async def test_governed_executor_force_planning_blocks_write_tool() -> None:
    """Executor should enforce force-planning denial before handler execution."""
    registry = ToolRegistry()
    called = False

    async def _write(_args):
        nonlocal called
        called = True
        return {"summary": "wrote"}

    registry.register(
        ToolManifest(
            name="file_write",
            description="Write file",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            approval_mode=ApprovalMode.NEVER,
        ),
        _write,
    )
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="write",
        run_id="run_force_planning_blocks",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            metadata={"force_planning": {"enabled": True}},
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="file_write", args={"path": "x"})]
        )
    )

    result = await executor.execute(run_input, response)

    assert called is False
    assert result.traces[0].status.value == "denied"
    assert result.traces[0].error_code == "policy_denied"
    assert "approved plan" in (result.envelopes[0].error.message or "")
    structured = result.envelopes[0].structured_output
    assert structured is not None
    assert structured["error_kind"] == "force_planning_required"
    assert structured["blocked_tool"] == "file_write"
    assert "exit_plan_mode_v2" in structured["next_tools"]
    assert "enter plan mode" in structured["remediation"]


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
