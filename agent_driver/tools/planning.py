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
    return {
        "summary": summary,
        "applied_args": args,
        "planning_step": step.model_dump(mode="json") if step is not None else None,
    }


def register_planning_tool(registry: ToolRegistry) -> None:
    """Register default planning-state update tool when absent."""
    if registry.get("planning_state_update") is not None:
        return
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


__all__ = [
    "apply_planning_state_tool_update",
    "planning_state_update_tool",
    "register_planning_tool",
]
