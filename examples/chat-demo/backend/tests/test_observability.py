"""Tests for chat-demo observability adapter."""

from __future__ import annotations

from app.observability import run_attributes

from agent_driver.contracts import AgentRunInput, ToolPolicyInput


def test_run_attributes_include_task_contract_and_tool_choice() -> None:
    run_input = AgentRunInput(
        input="найди в интернете источник",
        run_id="run_obs_chat",
        thread_id="thread_obs",
        agent_id="chat-demo-agent",
        graph_preset="single_react",
        tool_choice={"type": "tool", "name": "web_search"},
        tool_policy=ToolPolicyInput(
            metadata={
                "task_contract": {
                    "kind": "research",
                    "requires_research": True,
                },
                "planning_hint": {"level": "suggested"},
            }
        ),
        app_metadata={
            "session_id": "session_obs",
            "chat_mode": True,
            "scenario_id": "research-report",
        },
    )

    attrs = run_attributes(run_input)

    assert attrs["agent.run_id"] == "run_obs_chat"
    assert attrs["chat.session_id"] == "session_obs"
    assert attrs["agent_driver.scenario"] == "research-report"
    assert attrs["task_contract.kind"] == "research"
    assert attrs["task_contract.requires_research"] is True
    assert "web_search" in str(attrs["tool_choice.effective"])
