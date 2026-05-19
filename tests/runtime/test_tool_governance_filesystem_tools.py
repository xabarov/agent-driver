"""Integration tests for built-in filesystem tools via governed executor."""

from __future__ import annotations

import json

import pytest

from agent_driver.contracts import ToolCall, ToolPolicyInput, ToolPolicyMode
from tests.support.governed_tool_harness import (
    build_governed_filesystem_executor,
    default_run_input,
    execute_planned_tool,
)


@pytest.mark.asyncio
async def test_governed_executor_runs_builtin_read_file(tmp_path) -> None:
    """Governed executor should run built-in read_file and emit completed trace."""
    target = tmp_path / "doc.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    executor, _registry = build_governed_filesystem_executor()
    result = await execute_planned_tool(
        executor,
        default_run_input(run_id="run_builtin_read_file"),
        ToolCall(tool_name="read_file", args={"path": str(target)}),
    )
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
    executor, _registry = build_governed_filesystem_executor()
    run_input = default_run_input(
        run_id="run_builtin_read_file_denied",
        denied_tools=["read_file"],
    )
    result = await execute_planned_tool(
        executor,
        run_input,
        ToolCall(tool_name="read_file", args={"path": str(target)}),
    )
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
    executor, _registry = build_governed_filesystem_executor()
    run_input = default_run_input(
        run_id="run_builtin_write_interrupt",
        input_text="write file",
    ).model_copy(
        update={
            "tool_policy": ToolPolicyInput(
                mode=ToolPolicyMode.ALLOW_TOOLS,
                approval_required_for_risk="medium",
            )
        }
    )
    result = await execute_planned_tool(
        executor,
        run_input,
        ToolCall(
            tool_name="file_write",
            args={"path": str(target), "content": "alpha\n"},
        ),
    )
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
    executor, _registry = build_governed_filesystem_executor()
    result = await execute_planned_tool(
        executor,
        default_run_input(run_id="run_builtin_notebook_edit", input_text="edit notebook"),
        ToolCall(
            tool_name="notebook_edit",
            args={
                "path": str(target),
                "cell_idx": 0,
                "is_new_cell": False,
                "old_text": "old",
                "new_text": "new",
            },
        ),
    )
    assert result.interrupt is None
    assert result.traces
    assert result.traces[0].status.value == "completed"
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert rendered["cells"][0]["source"] == ["print('new')\n"]


@pytest.mark.asyncio
async def test_governed_executor_interrupts_for_bash_under_medium_risk() -> None:
    """bash builtin should interrupt when medium-risk approval threshold is active."""
    executor, _registry = build_governed_filesystem_executor()
    run_input = default_run_input(
        run_id="run_builtin_bash_interrupt",
        input_text="run shell",
    ).model_copy(
        update={
            "tool_policy": ToolPolicyInput(
                mode=ToolPolicyMode.ALLOW_TOOLS,
                approval_required_for_risk="medium",
            )
        }
    )
    result = await execute_planned_tool(
        executor,
        run_input,
        ToolCall(tool_name="bash", args={"command": "echo hello"}),
    )
    assert result.interrupt is not None
    assert result.envelopes[0].decision.value == "interrupt"
