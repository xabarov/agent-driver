"""Governed executor tests for planning/todo/ask-user tools."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall, ToolPolicyInput, ToolPolicyMode
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools import register_builtin_tools, register_planning_tool
from tests.runtime.conftest import llm_request_with_planned_calls


@pytest.mark.asyncio
async def test_governed_executor_applies_todo_write_into_planning_state() -> None:
    """todo_write should update planning_state metadata through runtime flow."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="write todos",
        run_id="run_planning_todo_write",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="todo_write",
                    args={
                        "merge": False,
                        "todos": [
                            {"id": "t1", "content": "step", "status": "in_progress"}
                        ],
                    },
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert result.envelopes[0].decision.value == "allow"
    structured = result.envelopes[0].structured_output
    assert isinstance(structured, dict)
    assert structured["applied_args"]["todo_items"][0]["id"] == "t1"


@pytest.mark.asyncio
async def test_governed_executor_interrupts_for_ask_user_question() -> None:
    """ask_user_question should produce clarification interrupt envelope."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="ask user",
        run_id="run_planning_ask_user",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="ask_user_question",
                    args={
                        "prompt": "Choose path",
                        "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                    },
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is not None
    assert result.interrupt.reason.value == "clarification_required"
    assert result.envelopes[0].decision.value == "interrupt"
