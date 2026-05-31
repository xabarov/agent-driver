"""Guard tests for chat plan-first policy in templates."""

from __future__ import annotations

from importlib import resources

from agent_driver.prompts.agent import (
    react_base_policy,
    react_chat_tool_policy,
    todo_write_guidance,
)


def test_react_chat_tool_policy_plan_without_prose_checklist() -> None:
    text = (
        resources.files("agent_driver.prompts.templates")
        .joinpath("react_chat_tool_policy_todo.txt")
        .read_text(encoding="utf-8")
    )
    assert "todo_write" in text
    assert "план" in text or "plan" in text.lower()
    assert "do not repeat" in text.lower() or "не повтор" in text.lower()
    policy = react_chat_tool_policy(available_tool_names=("todo_write",))
    assert "plan panel" in policy.lower() or "plan panel" in policy
    assert "numbered" not in policy.lower() or "do not repeat" in policy.lower()


def test_react_base_policy_todo_lifecycle() -> None:
    policy = react_base_policy()
    assert "merge=true" in policy
    assert "plan panel" in policy.lower()
    assert "do not copy" in policy.lower() or "do not repeat" in policy.lower()


def test_todo_write_guidance_template_exists() -> None:
    guidance = todo_write_guidance()
    assert "merge=true" in guidance
    assert "plan panel" in guidance.lower()
