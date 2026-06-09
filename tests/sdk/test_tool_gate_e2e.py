"""End-to-end tests for A0.2 ``tool_gate`` through the SDK facade.

Verifies that ``Agent.run(..., tool_gate=...)`` reaches the governed
tool executor (via the runner + steps mixin) and surfaces the gate's
decision in the run output.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import (
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
)
from agent_driver.sdk import Agent, create_agent
from agent_driver.tools import ToolSet


def _build_agent() -> Agent:
    return create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("web_search"),
    )


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="Search once.",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy={
            "metadata": {
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="web_search",
                        args={
                            "query": "agent driver",
                            "mock_results": [
                                {
                                    "title": "A",
                                    "url": "https://example.com",
                                    "snippet": "B",
                                }
                            ],
                        },
                    ).model_dump(mode="json")
                ]
            }
        },
    )


@pytest.mark.asyncio
async def test_run_without_gate_executes_tool() -> None:
    """Baseline: no gate → tool runs as normal."""
    agent = _build_agent()
    output = await agent.run(_run_input("run_gate_e2e_none"))
    assert output.status.value == "completed"
    # tool_trace contains web_search call → confirms it actually executed
    assert any(t.tool_name == "web_search" for t in output.tool_trace)


@pytest.mark.asyncio
async def test_run_with_allow_gate_executes_tool() -> None:
    """``ToolGateAllow`` is identical to no gate at the run level."""
    agent = _build_agent()
    seen_contexts: list[ToolGateContext] = []

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        seen_contexts.append(ctx)
        return ToolGateAllow()

    output = await agent.run(_run_input("run_gate_e2e_allow"), tool_gate=gate)
    assert output.status.value == "completed"
    assert len(seen_contexts) == 1
    assert seen_contexts[0].tool_name == "web_search"
    assert seen_contexts[0].args["query"] == "agent driver"
    assert any(t.tool_name == "web_search" for t in output.tool_trace)


@pytest.mark.asyncio
async def test_run_with_deny_gate_blocks_tool_call() -> None:
    """``ToolGateDeny`` produces a denied tool trace; the run still
    completes (the LLM gets to re-plan)."""
    agent = _build_agent()

    async def gate(ctx: ToolGateContext) -> ToolGateDeny:
        return ToolGateDeny(reason="not allowed in test")

    output = await agent.run(_run_input("run_gate_e2e_deny"), tool_gate=gate)
    # The denied call surfaces in the trace as DENIED.
    web_search_traces = [t for t in output.tool_trace if t.tool_name == "web_search"]
    assert web_search_traces, "expected a web_search trace row"
    assert web_search_traces[0].status.value == "denied"


@pytest.mark.asyncio
async def test_run_with_ask_gate_pauses_with_interrupt() -> None:
    """``ToolGateAsk`` flips the run to PAUSED with an
    ``approval_required`` interrupt the host can surface."""
    agent = _build_agent()

    async def gate(ctx: ToolGateContext) -> ToolGateAsk:
        return ToolGateAsk(message="Please approve this web_search.")

    output = await agent.run(_run_input("run_gate_e2e_ask"), tool_gate=gate)
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert output.interrupt.reason.value == "approval_required"
    assert "Please approve" in output.interrupt.description


# ---------------------------------------------------------------------------
# R2: construction-time default tool_gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_gate_applies_without_per_call_gate() -> None:
    """A gate passed to create_agent applies on run() with no per-call gate."""
    seen: list[ToolGateContext] = []

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        seen.append(ctx)
        return ToolGateAllow()

    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("web_search"),
        tool_gate=gate,
    )
    output = await agent.run(_run_input("run_default_gate"))
    assert output.status.value == "completed"
    assert len(seen) == 1 and seen[0].tool_name == "web_search"


@pytest.mark.asyncio
async def test_per_call_gate_overrides_default() -> None:
    """An explicit per-call gate wins over the construction-time default."""
    default_seen: list[ToolGateContext] = []
    call_seen: list[ToolGateContext] = []

    async def default_gate(ctx: ToolGateContext) -> ToolGateAllow:
        default_seen.append(ctx)
        return ToolGateAllow()

    async def call_gate(ctx: ToolGateContext) -> ToolGateAllow:
        call_seen.append(ctx)
        return ToolGateAllow()

    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("web_search"),
        tool_gate=default_gate,
    )
    await agent.run(_run_input("run_override_gate"), tool_gate=call_gate)
    assert len(call_seen) == 1
    assert default_seen == []  # default not consulted when a per-call gate is set


@pytest.mark.asyncio
async def test_default_deny_gate_blocks_tool() -> None:
    """A deny default gate blocks the planned call without per-call wiring."""

    async def gate(ctx: ToolGateContext) -> ToolGateDeny:
        return ToolGateDeny(reason="blocked by default gate")

    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("web_search"),
        tool_gate=gate,
    )
    output = await agent.run(_run_input("run_default_deny"))
    web_search_traces = [t for t in output.tool_trace if t.tool_name == "web_search"]
    assert web_search_traces
    assert web_search_traces[0].status.value == "denied"
