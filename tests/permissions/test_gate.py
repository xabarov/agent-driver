"""Permission gate: unit mapping + integration via the tool_gate seam."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.permissions import (
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
)
from agent_driver.runtime.tool_gate import (
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
)
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def _ctx(command: str, tool: str = "bash") -> ToolGateContext:
    return ToolGateContext(
        tool_name=tool,
        args={"command": command},
        run_id="r1",
        thread_id="t1",
        agent_id="agent",
        risk="medium",
        side_effect="external",
        current_tool_calls=0,
    )


@pytest.mark.asyncio
async def test_gate_maps_decisions() -> None:
    gate = build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD))
    assert isinstance(await gate(_ctx("rm -rf /")), ToolGateDeny)
    assert isinstance(await gate(_ctx("sudo apt-get install x")), ToolGateAsk)
    assert isinstance(await gate(_ctx("ls -la")), ToolGateAllow)


def _bash_run_input(run_id: str, command: str) -> AgentRunInput:
    return AgentRunInput(
        input="run a command",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy={
            "metadata": {
                "planned_tool_calls": [
                    ToolCall(tool_name="bash", args={"command": command}).model_dump(
                        mode="json"
                    )
                ]
            }
        },
    )


@pytest.mark.asyncio
async def test_gate_denies_dangerous_command_through_runner() -> None:
    """A CRITICAL command is denied before the bash handler runs."""
    agent = create_agent(
        provider=FakeProvider(response_text="ok"), tools=ToolSet.only("bash")
    )
    gate = build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD))

    output = await agent.run(
        _bash_run_input("run_perm_deny", "rm -rf /"), tool_gate=gate
    )
    bash_traces = [t for t in output.tool_trace if t.tool_name == "bash"]
    assert bash_traces, "expected a bash trace row"
    assert bash_traces[0].status.value == "denied"


@pytest.mark.asyncio
async def test_gate_asks_on_dangerous_command_through_runner() -> None:
    """A DANGEROUS command pauses the run for approval."""
    agent = create_agent(
        provider=FakeProvider(response_text="ok"), tools=ToolSet.only("bash")
    )
    gate = build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD))

    output = await agent.run(
        _bash_run_input("run_perm_ask", "sudo apt-get install nginx"), tool_gate=gate
    )
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert output.interrupt.reason.value == "approval_required"
