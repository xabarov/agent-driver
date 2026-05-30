"""Tests for planning/todo/ask-user built-in tools."""

from __future__ import annotations

import pytest

from agent_driver.context import planning_state_init
from agent_driver.tools.planning import (
    apply_planning_state_tool_update,
    register_planning_tool,
)
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_todo_write_validates_single_in_progress_and_returns_applied_args() -> (
    None
):
    """todo_write should enforce one in_progress item and return normalized args."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("todo_write")
    assert tool is not None
    out = await tool.handler(
        {
            "merge": False,
            "todos": [
                {"id": "t1", "content": "step 1", "status": "in_progress"},
                {"id": "t2", "content": "step 2", "status": "pending"},
            ],
        }
    )
    applied = out["applied_args"]
    assert applied["todo_merge"] is False
    assert len(applied["todo_items"]) == 2


@pytest.mark.asyncio
async def test_ask_user_question_returns_interrupt_payload_shape() -> None:
    """ask_user_question should return prompt and normalized choices."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("ask_user_question")
    assert tool is not None
    out = await tool.handler(
        {
            "prompt": "Choose mode",
            "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "allow_multiple": False,
        }
    )
    assert out["interrupt_reason"] == "clarification_required"
    assert out["prompt"] == "Choose mode"
    assert len(out["choices"]) == 2
    assert out["questions"] == [
        {
            "id": "q1",
            "header": "Clarify",
            "question": "Choose mode",
            "choices": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        }
    ]


@pytest.mark.asyncio
async def test_ask_user_question_validates_structured_questions() -> None:
    """Structured clarification should be bounded and preserve option metadata."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("ask_user_question")
    assert tool is not None
    question_schema = tool.manifest.args_schema["properties"]["questions"]["items"]
    assert question_schema["properties"]["header"]["maxLength"] == 12
    out = await tool.handler(
        {
            "prompt": "Pick report scope",
            "questions": [
                {
                    "id": "scope",
                    "header": "Scope",
                    "question": "Which scope should I use?",
                    "preview": "This affects source selection.",
                    "choices": [
                        {
                            "id": "history",
                            "label": "Company history",
                            "description": "Focus on Fender timeline.",
                        },
                        {"id": "models", "label": "Model overview"},
                    ],
                }
            ],
        }
    )
    assert out["choices"] == [
        {"id": "history", "label": "Company history"},
        {"id": "models", "label": "Model overview"},
    ]
    assert out["questions"][0]["header"] == "Scope"
    assert out["questions"][0]["choices"][0]["description"] == (
        "Focus on Fender timeline."
    )


@pytest.mark.asyncio
async def test_ask_user_question_rejects_unbounded_question_sets() -> None:
    """Clarification should stay short enough for a human to answer."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("ask_user_question")
    assert tool is not None
    with pytest.raises(ValueError, match="questions must contain 1-4 items"):
        await tool.handler(
            {
                "prompt": "Too many",
                "questions": [
                    {
                        "header": f"Q{index}",
                        "question": "Pick one",
                        "choices": [
                            {"id": "a", "label": "A"},
                            {"id": "b", "label": "B"},
                        ],
                    }
                    for index in range(5)
                ],
            }
        )


@pytest.mark.asyncio
async def test_ask_user_question_rejects_long_headers() -> None:
    """Clarification headers should stay compact enough for the chat UI."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    tool = registry.get("ask_user_question")
    assert tool is not None
    with pytest.raises(ValueError, match="12 characters or fewer"):
        await tool.handler(
            {
                "prompt": "Pick one",
                "questions": [
                    {
                        "header": "Very long header",
                        "question": "Pick one",
                        "choices": [
                            {"id": "a", "label": "A"},
                            {"id": "b", "label": "B"},
                        ],
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_enter_and_exit_plan_mode_tools_return_applied_args() -> None:
    """Mode-switch tools should map directly to planning_mode applied args."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    enter = registry.get("enter_plan_mode")
    exit_v2 = registry.get("exit_plan_mode_v2")
    assert enter is not None
    assert exit_v2 is not None
    entered = await enter.handler({"reason": "need architecture pass"})
    exited = await exit_v2.handler(
        {"reason": "ready to implement", "content": "1. Inspect\n2. Change"}
    )
    assert entered["applied_args"]["planning_mode"] == "plan"
    assert entered["planning_state"]["mode"] == "plan"
    assert exited["applied_args"]["planning_mode"] == "agent"
    assert exited["planning_state"]["mode"] == "agent"
    assert exited["interrupt_reason"] == "plan_approval_required"
    assert exited["plan_approval"]["content_hash"]


def test_apply_planning_state_tool_update_applies_todo_items_and_mode() -> None:
    """planning helper should apply todo_items and planning_mode."""
    state = planning_state_init("run_plan_tools")
    updated = apply_planning_state_tool_update(
        state,
        {
            "todo_items": [
                {"id": "t1", "content": "a", "status": "pending"},
                {"id": "t2", "content": "b", "status": "completed"},
            ],
            "todo_merge": False,
            "planning_mode": "plan",
        },
    )
    assert len(updated.todos) == 2
    assert updated.metadata["planning_mode"] == "plan"


def test_apply_planning_state_tool_update_merges_status_without_content() -> None:
    """merge=true status rows should update existing todos without repeating content."""
    state = apply_planning_state_tool_update(
        planning_state_init("run_plan_merge"),
        {
            "todo_items": [
                {
                    "id": "research",
                    "content": "Research topic",
                    "status": "in_progress",
                },
                {"id": "outline", "content": "Create outline", "status": "pending"},
            ],
            "todo_merge": False,
        },
    )
    updated = apply_planning_state_tool_update(
        state,
        {
            "todo_items": [
                {"id": "research", "status": "completed"},
                {"id": "outline", "status": "in_progress"},
            ],
            "todo_merge": True,
        },
    )
    rows = {item.todo_id: item for item in updated.todos}
    assert rows["research"].content == "Research topic"
    assert rows["research"].status.value == "completed"
    assert rows["outline"].content == "Create outline"
    assert rows["outline"].status.value == "in_progress"
