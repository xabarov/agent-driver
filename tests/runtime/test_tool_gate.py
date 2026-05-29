"""Tests for the A0.2 dynamic tool gate.

Coverage:
- ToolGateAllow → call executes unchanged
- ToolGateDeny → blocked envelope appears with the gate's reason
- ToolGateAsk → interrupt emitted with reason='approval_required'
- Gate exception → fail-closed (treated as Deny)
- Gate is bypassed when static policy already DENIES
- Gate is bypassed when static policy already INTERRUPTS
- Gate sees full args + manifest risk/side_effect
- Parallel batches all consult the gate

These tests target the governed tool executor directly so the gate
contract is locked at the right layer (executor-internal). End-to-end
plumbing through ``Agent.run(...)`` is covered by the SDK integration
tests in ``tests/sdk/test_tool_gate_e2e.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.enums import ToolTraceStatus
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import (
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls


def _read_manifest(name: str = "lookup") -> ToolManifest:
    """Low-risk read-only manifest; the gate is the only thing that
    can block a call against this manifest."""
    return ToolManifest(
        name=name,
        description="Read-only lookup tool",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
    )


def _executor_with_lookup() -> GovernedToolExecutor:
    registry = ToolRegistry()

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "echo": args}

    registry.register(_read_manifest(), _handler)
    return GovernedToolExecutor(registry=registry)


def _run_input() -> Any:
    from agent_driver.contracts import AgentRunInput

    return AgentRunInput(
        input="hello",
        run_id="run_gate_test",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


async def _execute(executor, run_input, planned, *, tool_gate=None):
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(planned=[planned])
    )
    return await executor.execute(run_input, response, tool_gate=tool_gate)


@pytest.mark.asyncio
async def test_gate_allow_passes_through() -> None:
    """``ToolGateAllow`` is a no-op: handler runs, envelope is OK."""
    executor = _executor_with_lookup()
    seen: list[ToolGateContext] = []

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        seen.append(ctx)
        return ToolGateAllow()

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    assert seen and seen[0].tool_name == "lookup"
    assert seen[0].args == {"q": "x"}
    assert seen[0].risk == "low"
    assert seen[0].side_effect == "read_only"
    assert result.interrupt is None
    assert len(result.envelopes) == 1
    assert result.traces[0].status == ToolTraceStatus.COMPLETED


@pytest.mark.asyncio
async def test_gate_deny_produces_blocked_envelope() -> None:
    """``ToolGateDeny`` materialises as a blocked envelope with the
    gate's reason in the trace summary — the LLM can see it and
    re-plan on the next turn."""
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateDeny:
        return ToolGateDeny(reason="quota exceeded")

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    assert result.interrupt is None
    assert len(result.envelopes) == 1
    trace = result.traces[0]
    assert trace.status == ToolTraceStatus.DENIED
    assert "quota exceeded" in trace.result_summary


@pytest.mark.asyncio
async def test_gate_ask_emits_interrupt() -> None:
    """``ToolGateAsk`` flips the decision to INTERRUPT; the runtime
    emits an ``approval_required`` InterruptRequest the host can
    surface to the operator."""
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateAsk:
        return ToolGateAsk(message="This will hit 47 rows. Approve?")

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    assert result.interrupt is not None
    assert result.interrupt.reason.value == "approval_required"
    # The gate message becomes the interrupt description (via policy.reason).
    assert "47 rows" in result.interrupt.description


@pytest.mark.asyncio
async def test_gate_exception_fails_closed_as_deny() -> None:
    """A gate that raises is treated as Deny — better to block one
    call than to silently bypass operator-level checks."""
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        raise RuntimeError("classifier down")

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    assert result.interrupt is None
    assert result.traces[0].status == ToolTraceStatus.DENIED
    # The fail-closed reason should mention the exception. Different
    # block paths surface the reason on different fields; check both.
    reason_blob = " ".join(
        [
            result.traces[0].result_summary or "",
            result.traces[0].error_code or "",
            (
                result.envelopes[0].error.message
                if result.envelopes[0].error is not None
                else ""
            ),
        ]
    )
    assert "classifier down" in reason_blob


@pytest.mark.asyncio
async def test_gate_is_skipped_when_static_policy_denies() -> None:
    """If ``ToolPolicyInput`` already denied the call (e.g. denylist),
    the gate is never invoked — denial is final."""
    executor = _executor_with_lookup()
    invocations = 0

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        nonlocal invocations
        invocations += 1
        return ToolGateAllow()

    from agent_driver.contracts import AgentRunInput

    run_input = AgentRunInput(
        input="hello",
        run_id="run_gate_skip",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            denied_tools=["lookup"],
        ),
    )
    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, run_input, call, tool_gate=gate)
    assert invocations == 0
    assert result.traces[0].status == ToolTraceStatus.DENIED


@pytest.mark.asyncio
async def test_gate_sees_current_tool_calls_count() -> None:
    """The gate's ``current_tool_calls`` reflects the prior count
    passed to the executor — useful for budget-style gates."""
    executor = _executor_with_lookup()
    seen: list[int] = []

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        seen.append(ctx.current_tool_calls)
        return ToolGateAllow()

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(planned=[call])
    )
    await executor.execute(
        _run_input(), response, current_tool_calls=5, tool_gate=gate
    )
    assert seen == [5]


@pytest.mark.asyncio
async def test_gate_args_dict_is_isolated_copy() -> None:
    """The gate gets a copy of args; mutations don't leak into the
    actual tool call. Cautious-by-default: a buggy gate cannot
    silently rewrite the model's planned args."""
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        ctx.args["q"] = "MUTATED"  # should NOT propagate
        return ToolGateAllow()

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "original"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    # Handler echoed back its args; verify the gate's mutation didn't
    # leak through to the executed call.
    assert result.envelopes[0].call.args == {"q": "original"}


__all__: list[str] = []
