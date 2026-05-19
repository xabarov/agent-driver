"""Optional live smoke tests (split by concern)."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ResumeAction,
    ResumeCommand,
    ToolCall,
    ToolRisk,
)
from tests.support.live_harness import (
    assert_live_interrupt_for_tool,
    build_live_runner,
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


@pytest.mark.asyncio
async def test_live_agent_run_with_todo_write_updates_planning_state() -> None:
    """Live lane should apply todo_write and persist todo row in planning metadata."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about todo write verification.",
            run_id="run_live_agent_tool_todo_write_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="todo_write",
                            args={
                                "merge": False,
                                "todos": [
                                    {
                                        "id": "todo_live_1",
                                        "content": "Verify live todo path",
                                        "status": "in_progress",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "todo_write")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    applied_args = structured.get("applied_args")
    assert isinstance(applied_args, dict)
    todo_items = applied_args.get("todo_items")
    assert isinstance(todo_items, list)
    assert todo_items and todo_items[0]["id"] == "todo_live_1"
    planning_state = output.metadata.get("planning_state")
    if isinstance(planning_state, dict):
        todos = planning_state.get("todos")
        assert isinstance(todos, list)
        assert todos and todos[0].get("todo_id") == "todo_live_1"


@pytest.mark.asyncio
async def test_live_agent_run_interrupts_for_ask_user_question_when_approval_required() -> (
    None
):
    """Live lane should interrupt ask_user_question under medium approval threshold."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_ask_user_question_interrupt",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="ask_user_question",
                            args={
                                "prompt": "Choose one option",
                                "choices": [
                                    {"id": "a", "label": "Option A"},
                                    {"id": "b", "label": "Option B"},
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert output.interrupt.reason.value in {
        "approval_required",
        "clarification_required",
    }
    approval_payload = output.metadata.get("approval_payload")
    assert isinstance(approval_payload, dict)
    assert approval_payload.get("tool_name") == "ask_user_question"
    assert any(
        item.tool_name == "ask_user_question" and item.status.value == "denied"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
async def test_live_agent_run_with_skill_tool_discovery(tmp_path) -> None:
    """Live lane should execute skill_tool and return discovered rows."""
    base_url, model, api_key = require_live_openrouter_config()
    skill_file = tmp_path / "skills" / "live" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Live Skill\n", encoding="utf-8")
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about skill discovery verification.",
            run_id="run_live_agent_tool_skill_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="skill_tool",
                            args={
                                "base_dir": str(tmp_path),
                                "trusted_roots": [str(tmp_path / "skills")],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "skill_tool")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    skills = structured.get("skills")
    assert isinstance(skills, list)
    assert skills and skills[0]["relative_path"] == "skills/live/SKILL.md"
    assert skills[0]["trusted"] is True


@pytest.mark.asyncio
async def test_live_agent_run_with_tool_search_discovery() -> None:
    """Live lane should execute tool_search and return filtered manifests."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about tool search verification.",
            run_id="run_live_agent_tool_tool_search_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="tool_search",
                            args={"query": "mcp", "risk": "medium", "max_results": 5},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "tool_search")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    tools = structured.get("tools")
    assert isinstance(tools, list)
    assert tools
    assert all("mcp" in str(row.get("name", "")) for row in tools)


@pytest.mark.asyncio
async def test_live_agent_run_with_brief_tool_payload() -> None:
    """Live lane should execute brief_tool and return message+attachments."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about brief tool verification.",
            run_id="run_live_agent_tool_brief_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="brief_tool",
                            args={
                                "message": "Daily summary",
                                "channel": "status",
                                "attachments": [
                                    {
                                        "artifact_id": "art_live_1",
                                        "kind": "tool_result",
                                        "sensitivity": "internal",
                                        "label": "summary.txt",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "brief_tool")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    brief = structured.get("brief")
    assert isinstance(brief, dict)
    assert brief.get("channel") == "status"
    attachments = brief.get("attachments")
    assert isinstance(attachments, list)
    assert attachments and attachments[0]["artifact_ref"]["artifact_id"] == "art_live_1"
