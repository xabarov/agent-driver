"""Planning-state tool handlers for governed execution path."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent_driver.context import (
    planning_state_set_step,
    planning_state_set_todo_status,
    planning_state_upsert_todo,
)
from agent_driver.context.planning import plan_content_hash
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
        existing_by_id = {item.todo_id: item for item in next_state.todos}
        for row in todo_items:
            if not isinstance(row, dict):
                continue
            todo_id = str(row.get("id") or row.get("todo_id") or "").strip()
            content = str(row.get("content") or "").strip()
            status_raw = str(row.get("status") or "pending").strip()
            if not content and todo_merge and todo_id in existing_by_id:
                content = existing_by_id[todo_id].content
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
        if not content and not merge:
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
    normalized_questions = _normalize_ask_user_questions(args, fallback_prompt=prompt)
    first_question = normalized_questions[0]
    normalized_choices = [
        {
            "id": str(choice["id"]),
            "label": str(choice["label"]),
        }
        for choice in first_question.get("choices", [])
    ]
    allow_multiple = bool(args.get("allow_multiple", False))
    return {
        "summary": "ask_user_question prepared interrupt payload",
        "prompt": prompt,
        "choices": normalized_choices,
        "questions": normalized_questions,
        "allow_multiple": allow_multiple,
        "interrupt_reason": InterruptReason.CLARIFICATION_REQUIRED.value,
    }


def _normalize_ask_user_questions(
    args: dict[str, Any], *, fallback_prompt: str
) -> list[dict[str, Any]]:
    questions = args.get("questions")
    if questions is None:
        choices = _normalize_ask_user_choices(args.get("choices"), required=False)
        return [
            {
                "id": "q1",
                "header": "Clarify",
                "question": fallback_prompt,
                "choices": choices,
            }
        ]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 4:
        raise ValueError("questions must contain 1-4 items")
    normalized: list[dict[str, Any]] = []
    seen_question_ids: set[str] = set()
    seen_headers: set[str] = set()
    for index, row in enumerate(questions, start=1):
        if not isinstance(row, dict):
            raise ValueError("question rows must be objects")
        question_id = str(row.get("id") or f"q{index}").strip()
        header = str(row.get("header") or "").strip()
        question = str(row.get("question") or "").strip()
        preview = str(row.get("preview") or "").strip()
        if not question_id or not header or not question:
            raise ValueError(
                "question.id, question.header, and question.question are required"
            )
        if len(header) > 12:
            raise ValueError("question.header must be 12 characters or fewer")
        if question_id in seen_question_ids:
            raise ValueError("question ids must be unique")
        if header.lower() in seen_headers:
            raise ValueError("question headers must be unique")
        seen_question_ids.add(question_id)
        seen_headers.add(header.lower())
        item: dict[str, Any] = {
            "id": question_id,
            "header": header,
            "question": question,
            "choices": _normalize_ask_user_choices(row.get("choices"), required=True),
        }
        if preview:
            item["preview"] = preview
        normalized.append(item)
    return normalized


def _normalize_ask_user_choices(
    choices: object, *, required: bool
) -> list[dict[str, str]]:
    if choices is None and not required:
        return []
    if not isinstance(choices, list) or not 2 <= len(choices) <= 4:
        raise ValueError("choices must contain 2-4 items")
    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for row in choices:
        if not isinstance(row, dict):
            raise ValueError("choice rows must be objects")
        choice_id = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        description = str(row.get("description") or "").strip()
        if not choice_id or not label:
            raise ValueError("choice.id and choice.label are required")
        if choice_id in seen_ids:
            raise ValueError("choice ids must be unique")
        if label.lower() in seen_labels:
            raise ValueError("choice labels must be unique")
        seen_ids.add(choice_id)
        seen_labels.add(label.lower())
        item = {"id": choice_id, "label": label}
        if description:
            item["description"] = description
        normalized.append(item)
    return normalized


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
                                "content": {
                                    "type": "string",
                                    "description": (
                                        "Required for new todos; optional for "
                                        "merge=true status updates of existing todos."
                                    ),
                                },
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
                            "required": ["id", "status"],
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
            description=(
                "Create a bounded clarification request for genuinely blocking "
                "user-owned decisions. Prefer one focused question; use 1-4 "
                "questions only when separate decisions are required. Each "
                "structured question must have a short unique header (12 "
                "characters or fewer) and 2-4 unique options. Keep option "
                "labels short. Do not use this tool to ask whether a plan is "
                "approved or to avoid producing a requested deliverable."
            ),
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "choices": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["id", "label"],
                            "additionalProperties": False,
                        },
                    },
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "header": {"type": "string", "maxLength": 12},
                                "question": {"type": "string"},
                                "preview": {"type": "string"},
                                "choices": {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 4,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "label": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                        "required": ["id", "label"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["header", "question", "choices"],
                            "additionalProperties": False,
                        },
                    },
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
    content = str(args.get("content") or args.get("plan") or "").strip()
    path = str(args.get("path") or "").strip() or None
    plan_id = str(args.get("plan_id") or f"plan_{uuid4().hex[:12]}").strip()
    summary = "exited plan mode"
    if reason:
        summary = f"exited plan mode: {reason}"
    approval_payload = None
    if content:
        approval_payload = {
            "plan_id": plan_id,
            "content": content,
            "content_hash": plan_content_hash(content),
            "path": path,
        }
    return {
        "summary": summary,
        "applied_args": {"planning_mode": "agent", "plan_id": plan_id},
        "planning_state": {"mode": "agent"},
        "plan_approval": approval_payload,
        "interrupt_reason": (
            InterruptReason.PLAN_APPROVAL_REQUIRED.value if content else None
        ),
    }


def _register_enter_plan_mode_tool(registry: ToolRegistry) -> None:
    if registry.get("enter_plan_mode") is not None:
        return
    registry.register(
        ToolManifest(
            name="enter_plan_mode",
            description=(
                "Switch planning state to plan mode for non-trivial implementation "
                "work before side-effecting execution."
            ),
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "content": {
                        "type": "string",
                        "description": "Plan content to present for approval.",
                    },
                    "plan": {
                        "type": "string",
                        "description": "Alias for content.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional plan artifact path.",
                    },
                },
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
            description=(
                "Present a concrete plan for approval and switch planning state "
                "back to agent mode."
            ),
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.NONE,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "plan_id": {"type": "string"},
                    "content": {
                        "type": "string",
                        "description": "Approval-ready plan content.",
                    },
                    "plan": {
                        "type": "string",
                        "description": "Alias for content.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional plan artifact path.",
                    },
                },
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
