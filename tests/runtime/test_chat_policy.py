"""Tests for reusable chat-facing runtime policy helpers."""

from __future__ import annotations

from agent_driver.runtime.chat_policy import (
    build_chat_tool_policy,
    initial_tool_choice_for_chat,
    is_deliverable_request,
)


def test_deliverable_request_denies_replanning_tools() -> None:
    policy = build_chat_tool_policy("напиши реферат по истории Fender")

    assert is_deliverable_request("напиши реферат")
    assert policy.metadata["deliverable_request"] == {
        "enabled": True,
        "reason": "user asked to produce the deliverable now",
    }
    assert policy.denied_tools == [
        "ask_user_question",
        "enter_plan_mode",
        "exit_plan_mode_v2",
    ]


def test_force_planning_metadata_is_preserved() -> None:
    policy = build_chat_tool_policy(
        "составь план миграции",
        force_planning=True,
        force_planning_mode="required",
    )

    assert policy.metadata["force_planning"] == {
        "enabled": True,
        "mode": "required",
    }
    assert policy.metadata["planning_hint"]


def test_initial_tool_choice_for_research_requires_web_preset() -> None:
    policy = build_chat_tool_policy("найди в интернете информацию о Fender")

    assert policy.metadata["research_request"]["enabled"] is True
    assert policy.denied_tools == ["ask_user_question"]
    assert initial_tool_choice_for_chat(policy=policy, preset="web") == {
        "type": "tool",
        "name": "web_search",
    }
    assert initial_tool_choice_for_chat(policy=policy, preset="none") is None
    assert (
        initial_tool_choice_for_chat(
            policy=build_chat_tool_policy("сколько букв в слове strawberry?"),
            preset="web",
        )
        is None
    )


def test_initial_tool_choice_does_not_force_web_for_plan_only() -> None:
    policy = build_chat_tool_policy(
        "составь только план поиска информации по истории Fender, без реферата"
    )

    assert policy.metadata["task_contract"]["kind"] == "plan"
    assert policy.metadata["task_contract"]["requires_research"] is False
    assert policy.metadata["plan_only_request"]["enabled"] is True
    assert policy.denied_tools == ["web_search", "web_fetch"]
    assert initial_tool_choice_for_chat(policy=policy, preset="web") is None
