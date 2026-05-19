"""Optional live smoke tests (split by concern)."""

from __future__ import annotations

from uuid import uuid4

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


@pytest.mark.asyncio
async def test_live_agent_run_with_agent_tool_request_payload() -> None:
    """Live lane should execute agent_tool and return spawn request payload."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about agent tool verification.",
            run_id="run_live_agent_tool_agent_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="agent_tool",
                            args={
                                "task": "Summarize TODO progress for the sprint.",
                                "description": "Sprint status summary",
                                "execution_mode": "background",
                                "task_type": "summary",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "agent_tool")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    request = structured.get("subagent_request")
    assert isinstance(request, dict)
    assert request.get("execution_mode") == "background"
    assert request.get("description") == "Sprint status summary"


@pytest.mark.asyncio
async def test_live_agent_run_with_send_message_tool_payload() -> None:
    """Live lane should execute send_message_tool and return queued event."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about send message verification.",
            run_id="run_live_agent_tool_send_message_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="send_message_tool",
                            args={
                                "recipient": "agent.teammate",
                                "message": "Please check live run output.",
                                "thread_id": "thread_live",
                                "channel": "direct",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "send_message_tool")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    event = structured.get("message_event")
    assert isinstance(event, dict)
    assert event.get("recipient") == "agent.teammate"
    assert event.get("thread_id") == "thread_live"


@pytest.mark.asyncio
async def test_live_agent_run_with_list_peers_tool_payload() -> None:
    """Live lane should execute list_peers_tool and return filtered peers."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly about peers list verification.",
            run_id="run_live_agent_tool_list_peers_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="list_peers_tool",
                            args={"status": "online", "capability": "summary"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    env = tool_result(output, "list_peers_tool")
    assert env
    structured = env.get("structured_output")
    assert isinstance(structured, dict)
    peers = structured.get("peers")
    assert isinstance(peers, list)
    assert peers
    assert all(item["status"] == "online" for item in peers)


@pytest.mark.asyncio
async def test_live_agent_run_with_team_create_and_delete_tool_payloads() -> None:
    """Live lane should execute team create/delete tools in sequence."""
    base_url, model, api_key = require_live_openrouter_config()
    team_id = f"team_live_{uuid4().hex[:8]}"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    created = await runner.run(
        AgentRunInput(
            input="Reply briefly about team create verification.",
            run_id="run_live_agent_tool_team_create_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="team_create_tool",
                            args={
                                "team_id": team_id,
                                "display_name": "Live Team",
                                "members": ["agent.teammate", "agent.researcher"],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    created_env = tool_result(created, "team_create_tool")
    assert created_env
    created_structured = created_env.get("structured_output")
    assert isinstance(created_structured, dict)
    team = created_structured.get("team")
    assert isinstance(team, dict)
    assert team.get("team_id") == team_id
    deleted = await runner.run(
        AgentRunInput(
            input="Reply briefly about team delete verification.",
            run_id="run_live_agent_tool_team_delete_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="team_delete_tool",
                            args={"team_id": team_id},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    deleted_env = tool_result(deleted, "team_delete_tool")
    assert deleted_env
    deleted_structured = deleted_env.get("structured_output")
    assert isinstance(deleted_structured, dict)
    deleted_team = deleted_structured.get("deleted_team")
    assert isinstance(deleted_team, dict)
    assert deleted_team.get("team_id") == team_id


@pytest.mark.asyncio
async def test_live_agent_run_with_team_get_and_list_tool_payloads() -> None:
    """Live lane should execute team get/list tools after a create step."""
    base_url, model, api_key = require_live_openrouter_config()
    team_id = f"team_live_lookup_{uuid4().hex[:8]}"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    _ = await runner.run(
        AgentRunInput(
            input="Reply briefly about team create for get/list verification.",
            run_id="run_live_agent_tool_team_create_for_lookup",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="team_create_tool",
                            args={
                                "team_id": team_id,
                                "members": ["agent.teammate", "agent.researcher"],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    loaded = await runner.run(
        AgentRunInput(
            input="Reply briefly about team get verification.",
            run_id="run_live_agent_tool_team_get_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="team_get_tool",
                            args={"team_id": team_id},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    loaded_env = tool_result(loaded, "team_get_tool")
    assert loaded_env
    loaded_structured = loaded_env.get("structured_output")
    assert isinstance(loaded_structured, dict)
    loaded_team = loaded_structured.get("team")
    assert isinstance(loaded_team, dict)
    assert loaded_team.get("team_id") == team_id
    listed = await runner.run(
        AgentRunInput(
            input="Reply briefly about team list verification.",
            run_id="run_live_agent_tool_team_list_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="team_list_tool",
                            args={"member": "agent.teammate"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    listed_env = tool_result(listed, "team_list_tool")
    assert listed_env
    listed_structured = listed_env.get("structured_output")
    assert isinstance(listed_structured, dict)
    teams = listed_structured.get("teams")
    assert isinstance(teams, list)
    assert any(item.get("team_id") == team_id for item in teams)


@pytest.mark.asyncio
async def test_live_agent_run_with_task_stop_monitor_and_sleep_tools() -> None:
    """Live lane should execute task_stop/monitor/sleep tools deterministically."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    created = await runner.run(
        AgentRunInput(
            input="Reply briefly about task create for monitor flow.",
            run_id="run_live_agent_tool_task_create_for_monitor",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="task_create",
                            args={"title": "monitor-live"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    created_env = tool_result(created, "task_create")
    assert created_env
    created_structured = created_env.get("structured_output")
    assert isinstance(created_structured, dict)
    task = created_structured.get("task")
    assert isinstance(task, dict)
    task_id = str(task["task_id"])
    monitored = await runner.run(
        AgentRunInput(
            input="Reply briefly about task monitor verification.",
            run_id="run_live_agent_tool_monitor_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="monitor_tool",
                            args={"task_id": task_id},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    monitored_env = tool_result(monitored, "monitor_tool")
    assert monitored_env
    stopped = await runner.run(
        AgentRunInput(
            input="Reply briefly about task stop verification.",
            run_id="run_live_agent_tool_task_stop_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="task_stop_tool",
                            args={"task_id": task_id, "status": "killed"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    stopped_env = tool_result(stopped, "task_stop_tool")
    assert stopped_env
    slept = await runner.run(
        AgentRunInput(
            input="Reply briefly about sleep tool verification.",
            run_id="run_live_agent_tool_sleep_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="sleep_tool",
                            args={"seconds": 0.0},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    sleep_env = tool_result(slept, "sleep_tool")
    assert sleep_env


@pytest.mark.asyncio
async def test_live_agent_run_with_worktree_request_tools() -> None:
    """Live lane should execute worktree request-envelope tools."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    entered = await runner.run(
        AgentRunInput(
            input="Reply briefly about worktree enter verification.",
            run_id="run_live_agent_tool_enter_worktree_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="enter_worktree_tool",
                            args={"worktree_name": "feat-live"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    enter_env = tool_result(entered, "enter_worktree_tool")
    assert enter_env
    exited = await runner.run(
        AgentRunInput(
            input="Reply briefly about worktree exit verification.",
            run_id="run_live_agent_tool_exit_worktree_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="exit_worktree_tool",
                            args={"worktree_name": "feat-live"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    exit_env = tool_result(exited, "exit_worktree_tool")
    assert exit_env


@pytest.mark.asyncio
async def test_live_agent_run_with_automation_adapter_tools() -> None:
    """Live lane should execute representative automation adapter tools."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    wf = await runner.run(
        AgentRunInput(
            input="Reply briefly about workflow adapter verification.",
            run_id="run_live_agent_tool_workflow_adapter_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="workflow_tool",
                            args={"workflow_id": "wf_live"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert tool_result(wf, "workflow_tool")
    cron = await runner.run(
        AgentRunInput(
            input="Reply briefly about cron adapter verification.",
            run_id="run_live_agent_tool_cron_adapter_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="cron_create_tool",
                            args={
                                "job_name": f"job_{uuid4().hex[:6]}",
                                "schedule": "*/5 * * * *",
                                "command": "echo live",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert tool_result(cron, "cron_create_tool")
