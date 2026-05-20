"""Planning-state tool handlers for governed execution path."""

from __future__ import annotations

from typing import Any

from agent_driver.context import (
    planning_state_set_step,
    planning_state_set_todo_status,
    planning_state_upsert_todo,
)
from agent_driver.contracts.context import PlanningState, PlanningStep, TodoState
from agent_driver.contracts.enums import (
    ApprovalMode,
    InterruptReason,
    PlanningTodoStatus,
    SideEffectClass,
    ToolRisk,
)
from agent_driver.contracts.tools import ToolManifest
from agent_driver.tools.registry import ToolRegistry


def apply_planning_state_tool_update(
    state: PlanningState, args: dict[str, Any]
) -> PlanningState:
    """Apply deterministic planning updates from tool args."""
    next_state = state
    if isinstance(args.get("step"), dict):
        next_state = planning_state_set_step(
            next_state, PlanningStep.model_validate(args["step"])
        )
    if isinstance(args.get("todo"), dict):
        next_state = planning_state_upsert_todo(
            next_state, TodoState.model_validate(args["todo"])
        )
    if isinstance(args.get("todo_status"), dict):
        todo_status = args["todo_status"]
        todo_id = str(todo_status.get("todo_id", ""))
        status_raw = str(todo_status.get("status", "pending"))
        next_state = planning_state_set_todo_status(
            next_state,
            todo_id=todo_id,
            status=PlanningTodoStatus(status_raw),
        )
    todo_items = args.get("todo_items")
    if isinstance(todo_items, list):
        todo_merge = bool(args.get("todo_merge", False))
        if not todo_merge:
            next_state = next_state.model_copy(update={"todos": []})
        for row in todo_items:
            if not isinstance(row, dict):
                continue
            todo_id = str(row.get("id") or row.get("todo_id") or "").strip()
            content = str(row.get("content") or "").strip()
            status_raw = str(row.get("status") or "pending").strip()
            if not todo_id or not content:
                continue
            next_state = planning_state_upsert_todo(
                next_state,
                TodoState(
                    todo_id=todo_id,
                    content=content,
                    status=PlanningTodoStatus(status_raw),
                ),
            )
    planning_mode = args.get("planning_mode")
    if isinstance(planning_mode, str) and planning_mode in {"plan", "agent"}:
        next_state = next_state.model_copy(
            update={"metadata": {**next_state.metadata, "planning_mode": planning_mode}}
        )
    return next_state


async def planning_state_update_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Return normalized planning-state update payload for runtime merge."""
    step_payload = args.get("step")
    step = (
        PlanningStep.model_validate(step_payload)
        if isinstance(step_payload, dict)
        else None
    )
    summary = "planning updated"
    if step is not None:
        summary = f"planning updated: {step.next_plan}"
    planning_mode = (
        "plan"
        if bool(args.get("enter_plan_mode"))
        else ("agent" if bool(args.get("exit_plan_mode")) else None)
    )
    applied_args = dict(args)
    if planning_mode is not None:
        applied_args["planning_mode"] = planning_mode
    return {
        "summary": summary,
        "applied_args": applied_args,
        "planning_step": step.model_dump(mode="json") if step is not None else None,
        "planning_state": {"mode": planning_mode},
    }


def register_planning_tool(registry: ToolRegistry) -> None:
    """Register default planning-state update tool when absent."""
    if registry.get("planning_state_update") is None:
        registry.register(
            ToolManifest(
                name="planning_state_update",
                description="Update planning/todo state for subsequent turns.",
                risk=ToolRisk.LOW,
                side_effect=SideEffectClass.NONE,
                approval_mode=ApprovalMode.NEVER,
            ),
            planning_state_update_tool,
        )
    _register_todo_write_tool(registry)
    _register_ask_user_question_tool(registry)
    _register_enter_plan_mode_tool(registry)
    _register_exit_plan_mode_v2_tool(registry)


def build_todo_write_summary_and_next_action(
    todos: list[dict[str, str]],
) -> tuple[str, str]:
    """Build model-facing summary and next_action from normalized todo rows."""
    total = len(todos)
    completed = sum(1 for row in todos if row["status"] == "completed")
    in_progress = [row for row in todos if row["status"] == "in_progress"]
    if total == 0:
        return "todo_write: empty list", "Add todos with id, content, and status."
    if completed == total:
        return (
            f"todo_write: {completed}/{total} completed. All steps done.",
            "All plan steps are completed.",
        )
    if len(in_progress) == 1:
        active = in_progress[0]
        short = active["content"]
        if len(short) > 48:
            short = f"{short[:45]}..."
        summary = (
            f"todo_write: {completed}/{total} done, in_progress={active['id']}. "
            "Plan panel updated; do not repeat the checklist in chat."
        )
        next_action = (
            f"When step '{active['id']}' ({active['content']}) is finished, call "
            "todo_write with merge=true: mark it completed and set the next "
            "step in_progress before more tools."
        )
        return summary, next_action
    summary = (
        f"todo_write: {completed}/{total} done. "
        "Set exactly one todo in_progress. Plan panel updated."
    )
    return summary, "Set exactly one todo to in_progress before starting work."


async def _todo_write_tool(args: dict[str, Any]) -> dict[str, Any]:
    todos_raw = args.get("todos")
    if not isinstance(todos_raw, list) or not todos_raw:
        raise ValueError("todos must be a non-empty list")
    merge = bool(args.get("merge", False))
    normalized: list[dict[str, str]] = []
    for row in todos_raw:
        if not isinstance(row, dict):
            raise ValueError("todos rows must be objects")
        todo_id = str(row.get("id") or "").strip()
        content = str(row.get("content") or "").strip()
        status = str(row.get("status") or "pending").strip()
        if not todo_id:
            raise ValueError("todo.id is required")
        if not content:
            raise ValueError("todo.content is required")
        if status not in {"pending", "in_progress", "completed", "cancelled"}:
            raise ValueError(
                "todo.status must be pending/in_progress/completed/cancelled"
            )
        normalized.append({"id": todo_id, "content": content, "status": status})
    in_progress_count = sum(1 for row in normalized if row["status"] == "in_progress")
    if in_progress_count > 1:
        raise ValueError("at most one todo can be in_progress")
    summary, next_action = build_todo_write_summary_and_next_action(normalized)
    return {
        "summary": summary,
        "next_action": next_action,
        "current_todos": normalized,
        "merge": merge,
        "applied_args": {
            "todo_items": normalized,
            "todo_merge": merge,
        },
        "structured": {
            "current_todos": normalized,
            "merge": merge,
            "next_action": next_action,
        },
    }


async def _ask_user_question_tool(args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")
    choices = args.get("choices")
    normalized_choices: list[dict[str, str]] = []
    if choices is not None:
        if not isinstance(choices, list) or len(choices) < 2:
            raise ValueError("choices must be a list with at least 2 items")
        for row in choices:
            if not isinstance(row, dict):
                raise ValueError("choice rows must be objects")
            choice_id = str(row.get("id") or "").strip()
            label = str(row.get("label") or "").strip()
            if not choice_id or not label:
                raise ValueError("choice.id and choice.label are required")
            normalized_choices.append({"id": choice_id, "label": label})
    allow_multiple = bool(args.get("allow_multiple", False))
    return {
        "summary": "ask_user_question prepared interrupt payload",
        "prompt": prompt,
        "choices": normalized_choices,
        "allow_multiple": allow_multiple,
        "interrupt_reason": InterruptReason.CLARIFICATION_REQUIRED.value,
    }


def _register_todo_write_tool(registry: ToolRegistry) -> None:
    if registry.get("todo_write") is not None:
        return
    registry.register(
        ToolManifest(
            name="todo_write",
            description=(
                "Maintain a visible multi-step plan in the chat plan panel. "
                "Use for plan/roadmap requests: create 3–7 steps, one in_progress. "
                "Mark in_progress before starting a step; mark completed immediately "
                "when done; use merge=true to update statuses. "
                "Do not repeat the full checklist in assistant messages. "
                "Statuses: pending, in_progress, completed, cancelled."
            ),
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            remediation_hints=[
                "Plan checklist is visible in the UI plan panel.",
                "Mark completed immediately after each step; use merge=true.",
                "Do not copy the full todo list into chat prose.",
            ],
            args_schema={
                "type": "object",
                "properties": {
                    "merge": {"type": "boolean"},
                    "todos": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": [
                                        "pending",
                                        "in_progress",
                                        "completed",
                                        "cancelled",
                                    ],
                                },
                            },
                            "required": ["id", "content", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["todos"],
                "additionalProperties": False,
            },
            output_type="json",
        ),
        _todo_write_tool,
    )


def _register_ask_user_question_tool(registry: ToolRegistry) -> None:
    if registry.get("ask_user_question") is not None:
        return
    registry.register(
        ToolManifest(
            name="ask_user_question",
            description="Create structured clarification request for user.",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "choices": {"type": "array"},
                    "allow_multiple": {"type": "boolean"},
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            output_type="json",
        ),
        _ask_user_question_tool,
    )


async def _enter_plan_mode_tool(args: dict[str, Any]) -> dict[str, Any]:
    reason = str(args.get("reason") or "").strip()
    summary = "entered plan mode"
    if reason:
        summary = f"entered plan mode: {reason}"
    return {
        "summary": summary,
        "applied_args": {"planning_mode": "plan"},
        "planning_state": {"mode": "plan"},
    }


async def _exit_plan_mode_v2_tool(args: dict[str, Any]) -> dict[str, Any]:
    reason = str(args.get("reason") or "").strip()
    summary = "exited plan mode"
    if reason:
        summary = f"exited plan mode: {reason}"
    return {
        "summary": summary,
        "applied_args": {"planning_mode": "agent"},
        "planning_state": {"mode": "agent"},
    }


def _register_enter_plan_mode_tool(registry: ToolRegistry) -> None:
    if registry.get("enter_plan_mode") is not None:
        return
    registry.register(
        ToolManifest(
            name="enter_plan_mode",
            description="Switch planning state to plan mode.",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "additionalProperties": False,
            },
            output_type="json",
        ),
        _enter_plan_mode_tool,
    )


def _register_exit_plan_mode_v2_tool(registry: ToolRegistry) -> None:
    if registry.get("exit_plan_mode_v2") is not None:
        return
    registry.register(
        ToolManifest(
            name="exit_plan_mode_v2",
            description="Switch planning state back to agent mode.",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "additionalProperties": False,
            },
            output_type="json",
        ),
        _exit_plan_mode_v2_tool,
    )


__all__ = [
    "apply_planning_state_tool_update",
    "planning_state_update_tool",
    "register_planning_tool",
    "_ask_user_question_tool",
    "_enter_plan_mode_tool",
    "_exit_plan_mode_v2_tool",
    "_todo_write_tool",
]
