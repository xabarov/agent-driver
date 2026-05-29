"""Optional live smoke tests (split by concern)."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ResumeAction, ResumeCommand, ToolCall, ToolRisk
from tests.support.live_harness import (
    assert_live_interrupt_for_tool,
    build_live_runner,
    require_live_openrouter_config,
)


@pytest.mark.asyncio
async def test_live_agent_run_interrupts_for_bash_when_approval_required() -> None:
    """Live lane should pause before bash when risk threshold requires approval."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_bash_interrupt",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash",
                            args={"command": "echo should-not-run"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert_live_interrupt_for_tool(output, "bash")


@pytest.mark.asyncio
async def test_live_agent_run_interrupts_for_file_write_when_approval_required(
    tmp_path,
) -> None:
    """Live lane should pause before file_write when risk threshold requires approval."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "blocked-write.txt"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_interrupt",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "blocked\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert_live_interrupt_for_tool(output, "file_write")
    assert not target.exists()


@pytest.mark.asyncio
async def test_live_agent_run_resume_approve_executes_pending_file_write(
    tmp_path,
) -> None:
    """Live HITL lane: approve resume should execute pending file_write once."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "resume-approve.txt"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    paused = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_resume_approve",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "approved\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert_live_interrupt_for_tool(paused, "file_write")
    assert paused.interrupt is not None
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_live_agent_tool_file_write_resume_approve",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent.live",
            graph_preset="single_react",
        )
    )
    assert resumed.status.value == "completed"
    assert target.read_text(encoding="utf-8") == "approved\n"
    assert any(
        item.tool_name == "file_write" and item.status.value == "completed"
        for item in resumed.tool_trace
    )


@pytest.mark.asyncio
async def test_live_agent_run_resume_reject_blocks_pending_file_write(tmp_path) -> None:
    """Live HITL lane: reject resume should keep side effect unapplied."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "resume-reject.txt"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    paused = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_resume_reject",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "rejected\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert_live_interrupt_for_tool(paused, "file_write")
    assert paused.interrupt is not None
    rejected = await runner.run(
        AgentRunInput(
            run_id="run_live_agent_tool_file_write_resume_reject",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
            ),
            agent_id="agent.live",
            graph_preset="single_react",
        )
    )
    assert rejected.status.value == "failed"
    assert rejected.terminal_reason is not None
    assert rejected.terminal_reason.value == "approval_rejected"
    assert not target.exists()


@pytest.mark.asyncio
async def test_live_agent_run_resume_edit_applies_edited_file_write_args(tmp_path) -> None:
    """Live HITL lane: edit resume should execute file_write with edited args."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "resume-edit.txt"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    paused = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_resume_edit",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "original\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    assert_live_interrupt_for_tool(paused, "file_write")
    assert paused.interrupt is not None
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_live_agent_tool_file_write_resume_edit",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.EDIT,
                edited_tool_args={"path": str(target), "content": "edited\n"},
            ),
            agent_id="agent.live",
            graph_preset="single_react",
        )
    )
    assert resumed.status.value == "completed"
    assert target.read_text(encoding="utf-8") == "edited\n"
    assert any(
        item.tool_name == "file_write" and item.status.value == "completed"
        for item in resumed.tool_trace
    )
