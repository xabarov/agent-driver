"""Optional live smoke tests (split by concern)."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ResumeAction, ResumeCommand, ToolCall, ToolRisk
from tests.support.live_harness import (
    assert_live_interrupt_for_tool,
    build_live_runner,
    notebook_fixture,
    require_live_openrouter_config,
    tool_result,
)


@pytest.mark.asyncio
async def test_live_agent_run_resume_cancel_blocks_pending_file_write(tmp_path) -> None:
    """Live HITL lane: cancel resume should keep side effect unapplied."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "resume-cancel.txt"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    paused = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_resume_cancel",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "cancelled\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert_live_interrupt_for_tool(paused, "file_write")
    assert paused.interrupt is not None
    cancelled = await runner.run(
        AgentRunInput(
            run_id="run_live_agent_tool_file_write_resume_cancel",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.CANCEL,
            ),
            agent_id="agent.live",
            graph_preset="single_react",
        )
    )
    assert cancelled.status.value == "cancelled"
    assert cancelled.terminal_reason is not None
    assert cancelled.terminal_reason.value == "cancelled_by_user"
    assert not target.exists()


@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_task_tools_flow() -> None:
    """Live lane should execute task_create + task_output + task_get chain."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    created = await runner.run(
        AgentRunInput(
            input="Reply briefly about task create.",
            run_id="run_live_agent_tool_task_create_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="task_create",
                            args={"title": "live-task"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    envelope = tool_result(created, "task_create")
    assert envelope
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    task = structured.get("task")
    assert isinstance(task, dict)
    task_id = str(task["task_id"])
    append_output = await runner.run(
        AgentRunInput(
            input="Reply briefly about task output.",
            run_id="run_live_agent_tool_task_output_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="task_output",
                            args={
                                "task_id": task_id,
                                "source": "stdout",
                                "text": "live monitor chunk",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert tool_result(append_output, "task_output")
    loaded = await runner.run(
        AgentRunInput(
            input="Reply briefly about task get.",
            run_id="run_live_agent_tool_task_get_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="task_get",
                            args={"task_id": task_id, "include_output": True},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    loaded_env = tool_result(loaded, "task_get")
    assert loaded_env
    loaded_structured = loaded_env.get("structured_output")
    assert isinstance(loaded_structured, dict)
    loaded_task = loaded_structured.get("task")
    assert isinstance(loaded_task, dict)
    output_rows = loaded_task.get("output")
    assert isinstance(output_rows, list)
    assert output_rows
    assert "live monitor chunk" in str(output_rows[0].get("text_preview", ""))


@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_mcp_resource_read() -> None:
    """Live lane should execute readonly MCP resource wrapper."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about MCP resource verification.",
            run_id="run_live_agent_tool_mcp_read_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="mcp_read_resource",
                            args={
                                "server": "demo-docs",
                                "resource_uri": "resource://docs/quickstart",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "mcp_read_resource")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    resource = structured.get("resource")
    assert isinstance(resource, dict)
    assert resource.get("resource_uri") == "resource://docs/quickstart"
    assert any(
        item.tool_name == "mcp_read_resource" and item.status.value == "completed"
        for item in output.tool_trace
    )
