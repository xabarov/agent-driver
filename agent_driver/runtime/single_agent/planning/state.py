"""Planning state updates during tool stages."""

from __future__ import annotations

import json
from uuid import uuid4

from agent_driver.context import (
    planning_state_init,
    planning_state_set_step,
    planning_state_upsert_todo,
)
from agent_driver.contracts.context import PlanningState, PlanningStep, TodoState
from agent_driver.contracts.enums import PlanningTodoStatus
from agent_driver.runtime.metadata_state import (
    PlanningRuntimeState,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    reset_todo_write_loop_counters,
)
from agent_driver.tools import apply_planning_state_tool_update

PLANNING_TOOL_NAMES = frozenset(
    {
        "planning_state_update",
        "todo_write",
        "continue_without_plan",
        "enter_plan_mode",
        "exit_plan_mode_v2",
    }
)

PLAN_REFINEMENT_EXIT_TOOL = "exit_plan_mode_v2"


def begin_plan_refinement(
    context: RunContext,
    *,
    interrupt_id: str,
    plan_payload: dict[str, object] | None,
) -> None:
    """Reopen plan mode and require a revised approval artifact."""
    planning_runtime = PlanningRuntimeState(context.metadata)
    planning_state_payload = planning_runtime.planning_state()
    if isinstance(planning_state_payload, dict):
        planning_state = PlanningState.model_validate(planning_state_payload)
    else:
        planning_state = planning_state_init(context.run_id)
    planning_state = planning_state.model_copy(
        update={
            "metadata": {
                **planning_state.metadata,
                "planning_mode": "plan",
            }
        }
    )
    planning_runtime.set_planning_state(planning_state.model_dump(mode="json"))

    current_policy = context.run_input.tool_policy
    payload = plan_payload or {}
    previous_allowed_tools = current_policy.allowed_tools
    refinement_payload = {
        "interrupt_id": interrupt_id,
        "plan_id": payload.get("plan_id"),
        "content_hash": payload.get("content_hash"),
        "previous_allowed_tools": previous_allowed_tools,
    }
    planning_runtime.require_plan_refinement(refinement_payload)

    policy_metadata = dict(current_policy.metadata)
    policy_metadata["plan_refinement_required"] = refinement_payload
    force_planning = policy_metadata.get("force_planning")
    if isinstance(force_planning, dict):
        force_planning = {**force_planning, "continue_without_plan": False}
        policy_metadata["force_planning"] = force_planning
    denied_tools = list(current_policy.denied_tools or [])
    if "continue_without_plan" not in denied_tools:
        denied_tools.append("continue_without_plan")
    context.run_input = context.run_input.model_copy(
        update={
            "tool_policy": current_policy.model_copy(
                update={
                    "metadata": policy_metadata,
                    "allowed_tools": [PLAN_REFINEMENT_EXIT_TOOL],
                    "denied_tools": denied_tools,
                }
            )
        }
    )
    tool_loop_state = get_tool_loop_state(context)
    tool_loop_state.clear_force_final_answer()
    tool_loop_state.set_tool_choice_override(
        {"type": "tool", "name": PLAN_REFINEMENT_EXIT_TOOL}
    )


def apply_planning_state_seed_from_metadata(context: RunContext) -> None:
    """Merge session planning seed from app_metadata into run planning_state."""
    planning_runtime = PlanningRuntimeState(context.metadata)
    seed = planning_runtime.pop_seed()
    if seed is None:
        return
    todos_raw = seed.get("todos")
    if not isinstance(todos_raw, list) or not todos_raw:
        return
    state = planning_state_init(context.run_id)
    for row in todos_raw:
        if not isinstance(row, dict):
            continue
        todo_id = str(row.get("id") or row.get("todo_id") or "").strip()
        content = str(row.get("content") or "").strip()
        status_raw = str(row.get("status") or "pending").strip()
        if not todo_id or not content:
            continue
        try:
            status = PlanningTodoStatus(status_raw)
        except ValueError:
            status = PlanningTodoStatus.PENDING
        state = planning_state_upsert_todo(
            state,
            TodoState(todo_id=todo_id, content=content, status=status),
        )
    planning_runtime.set_planning_state(state.model_dump(mode="json"))


def build_planning_snapshot(context: RunContext) -> dict[str, object] | None:
    """Build a TUI-friendly planning snapshot from run metadata."""
    payload = PlanningRuntimeState(context.metadata).planning_state()
    if not isinstance(payload, dict):
        return None
    state = PlanningState.model_validate(payload)
    if not state.todos:
        return None
    todos: list[dict[str, str]] = []
    in_progress_id: str | None = None
    completed = 0
    for item in state.todos:
        status = item.status.value
        if status == PlanningTodoStatus.IN_PROGRESS.value:
            in_progress_id = item.todo_id
        if status == PlanningTodoStatus.COMPLETED.value:
            completed += 1
        todos.append({"id": item.todo_id, "content": item.content, "status": status})
    plan_title: str | None = None
    in_progress_index: int | None = None
    if in_progress_id:
        for index, item in enumerate(state.todos, start=1):
            if item.todo_id == in_progress_id:
                text = item.content.strip()
                plan_title = text[:72] + ("..." if len(text) > 72 else "")
                in_progress_index = index
                break
    return {
        "todos": todos,
        "in_progress_id": in_progress_id,
        "in_progress_index": in_progress_index,
        "completed": completed,
        "total": len(todos),
        "plan_title": plan_title,
    }


def apply_planning_updates_from_envelopes(
    context: RunContext,
    result: ToolExecutionResult,
) -> bool:
    """Apply planning tool envelopes; return True if any tool updated planning."""
    planning_runtime = PlanningRuntimeState(context.metadata)
    planning_state_payload = planning_runtime.planning_state()
    if isinstance(planning_state_payload, dict):
        planning_state = PlanningState.model_validate(planning_state_payload)
    else:
        planning_state = planning_state_init(context.run_id)
    planning_updated_by_tool = False
    planning_runtime.clear_todo_deduped()
    for envelope in result.envelopes:
        if envelope.call.tool_name not in PLANNING_TOOL_NAMES:
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        if envelope.call.tool_name == "todo_write":
            applied_args = structured.get("applied_args")
            if isinstance(applied_args, dict):
                signature = json.dumps(applied_args, ensure_ascii=True, sort_keys=True)
                if planning_runtime.last_todo_write_signature() == signature:
                    structured["summary"] = (
                        "todo_write duplicate payload ignored; "
                        "use merge=true or change row statuses/content"
                    )
                    structured["applied_args"] = {
                        "todo_items": [],
                        "todo_merge": True,
                    }
                    envelope.summary = str(structured["summary"])
                    planning_runtime.mark_todo_deduped()
                    continue
                planning_runtime.set_last_todo_write_signature(signature)
        planning_updated_by_tool = True
        planning_state = apply_planning_state_tool_update(
            planning_state, structured.get("applied_args", {})
        )
        if envelope.call.tool_name == PLAN_REFINEMENT_EXIT_TOOL and isinstance(
            structured.get("plan_approval"), dict
        ):
            refinement = planning_runtime.plan_refinement() or {}
            current_policy = context.run_input.tool_policy
            policy_metadata = dict(current_policy.metadata)
            policy_metadata.pop("plan_refinement_required", None)
            context.run_input = context.run_input.model_copy(
                update={
                    "tool_policy": current_policy.model_copy(
                        update={
                            "allowed_tools": refinement.get("previous_allowed_tools"),
                            "metadata": policy_metadata,
                        }
                    )
                }
            )
            planning_runtime.clear_plan_refinement()
        if envelope.call.tool_name == "continue_without_plan":
            force_planning = context.run_input.tool_policy.metadata.get(
                "force_planning"
            )
            if (
                isinstance(force_planning, dict)
                and force_planning.get("mode") == "strategy_required_before_execution"
            ):
                force_planning["continue_without_plan"] = True
        if (
            envelope.call.tool_name == "todo_write"
            and not planning_runtime.is_todo_deduped()
        ):
            in_progress_id = None
            for item in planning_state.todos:
                if item.status == PlanningTodoStatus.IN_PROGRESS:
                    in_progress_id = item.todo_id
                    break
            reset_todo_write_loop_counters(context, in_progress_id=in_progress_id)
        if isinstance(structured.get("planning_step"), dict):
            planning_runtime.set_planning_step(structured["planning_step"])
    planning_runtime.set_planning_state(planning_state.model_dump(mode="json"))
    return planning_updated_by_tool


def update_planning_state_from_tool_results(context: RunContext) -> None:
    """Update minimal planning state and latest planning step payload."""
    tool_results = get_tool_loop_state(context).tool_results()
    facts_learned = [
        str(item.get("summary", ""))
        for item in tool_results
        if isinstance(item, dict) and isinstance(item.get("summary"), str)
    ]
    planning_step = PlanningStep(
        step_id=f"plan_{uuid4().hex[:8]}",
        facts_given=[context.run_input.input or ""],
        facts_learned=facts_learned[:3],
        facts_to_lookup=[],
        facts_to_derive=[],
        next_plan="Continue execution",
        metadata={"run_id": context.run_id},
    )
    planning_runtime = PlanningRuntimeState(context.metadata)
    planning_state_payload = planning_runtime.planning_state()
    if isinstance(planning_state_payload, dict):
        state = planning_state_set_step(
            PlanningState.model_validate(planning_state_payload), planning_step
        )
    else:
        state = planning_state_set_step(
            planning_state_init(context.run_id), planning_step
        )
    planning_runtime.set_planning_step(planning_step.model_dump(mode="json"))
    planning_runtime.set_planning_state(state.model_dump(mode="json"))


__all__ = [
    "PLAN_REFINEMENT_EXIT_TOOL",
    "PLANNING_TOOL_NAMES",
    "apply_planning_state_seed_from_metadata",
    "apply_planning_updates_from_envelopes",
    "begin_plan_refinement",
    "build_planning_snapshot",
    "update_planning_state_from_tool_results",
]
