"""Integration tests for built-in filesystem tools via governed executor."""

from __future__ import annotations

import json

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ToolCall,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools import register_builtin_tools
from tests.runtime.conftest import llm_request_with_planned_calls


@pytest.mark.asyncio
async def test_governed_executor_runs_builtin_read_file(tmp_path) -> None:
    """Governed executor should run built-in read_file and emit completed trace."""
    target = tmp_path / "doc.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="read file",
        run_id="run_builtin_read_file",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="read_file", args={"path": str(target)})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert len(result.traces) == 1
    assert result.traces[0].status.value == "completed"
    assert result.envelopes[0].summary is not None
    assert "doc.txt" in result.envelopes[0].summary


@pytest.mark.asyncio
async def test_governed_executor_denies_builtin_when_tool_not_allowed(tmp_path) -> None:
    """Policy deny list should block built-in tool execution."""
    target = tmp_path / "doc.txt"
    target.write_text("alpha\n", encoding="utf-8")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="read file",
        run_id="run_builtin_read_file_denied",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            denied_tools=["read_file"],
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="read_file", args={"path": str(target)})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert result.traces[0].status.value == "denied"
    assert result.envelopes[0].decision.value == "deny"
    assert result.envelopes[0].error is not None


@pytest.mark.asyncio
async def test_governed_executor_interrupts_for_medium_risk_builtin_write(
    tmp_path,
) -> None:
    """Risk-threshold policy should interrupt reversible write builtin tools."""
    target = tmp_path / "doc.txt"
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="write file",
        run_id="run_builtin_write_interrupt",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            approval_required_for_risk="medium",
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="file_write",
                    args={"path": str(target), "content": "alpha\n"},
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is not None
    assert result.traces
    assert result.traces[0].status.value == "denied"
    assert result.envelopes[0].decision.value == "interrupt"


@pytest.mark.asyncio
async def test_governed_executor_runs_notebook_edit_tool(tmp_path) -> None:
    """Governed executor should execute notebook_edit in allow-tools mode."""
    target = tmp_path / "note.ipynb"
    payload = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print('old')\n"],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    target.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="edit notebook",
        run_id="run_builtin_notebook_edit",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="notebook_edit",
                    args={
                        "path": str(target),
                        "cell_idx": 0,
                        "is_new_cell": False,
                        "old_text": "old",
                        "new_text": "new",
                    },
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert result.traces
    assert result.traces[0].status.value == "completed"
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert rendered["cells"][0]["source"] == ["print('new')\n"]


@pytest.mark.asyncio
async def test_governed_executor_interrupts_for_bash_under_medium_risk() -> None:
    """bash builtin should interrupt when medium-risk approval threshold is active."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="run shell",
        run_id="run_builtin_bash_interrupt",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            approval_required_for_risk="medium",
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="bash", args={"command": "echo hello"})]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is not None
    assert result.envelopes[0].decision.value == "interrupt"
