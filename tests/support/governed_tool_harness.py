"""Shared setup for governed filesystem tool integration tests."""

from __future__ import annotations

from agent_driver.contracts import AgentRunInput, ToolPolicyInput, ToolPolicyMode
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_builtin_tools
from tests.runtime.conftest import llm_request_with_planned_calls


def build_governed_filesystem_executor() -> tuple[GovernedToolExecutor, ToolRegistry]:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return GovernedToolExecutor(registry=registry), registry


def default_run_input(
    *,
    run_id: str,
    input_text: str = "read file",
    denied_tools: list[str] | None = None,
) -> AgentRunInput:
    policy = ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS)
    if denied_tools:
        policy = ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS, denied_tools=denied_tools)
    return AgentRunInput(
        input=input_text,
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=policy,
    )


async def execute_planned_tool(
    executor: GovernedToolExecutor,
    run_input: AgentRunInput,
    planned_call,
):
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(planned=[planned_call])
    )
    return await executor.execute(run_input, response)


__all__ = [
    "build_governed_filesystem_executor",
    "default_run_input",
    "execute_planned_tool",
]
