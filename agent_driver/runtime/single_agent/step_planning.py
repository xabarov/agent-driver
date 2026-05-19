"""Planning state updates during tool stages."""

from __future__ import annotations

from uuid import uuid4

from agent_driver.context import planning_state_init, planning_state_set_step
from agent_driver.contracts.context import PlanningState, PlanningStep
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult
from agent_driver.tools import apply_planning_state_tool_update

PLANNING_TOOL_NAMES = frozenset({"planning_state_update", "todo_write"})


def apply_planning_updates_from_envelopes(
    context: RunContext,
    result: ToolExecutionResult,
) -> bool:
    """Apply planning tool envelopes; return True if any tool updated planning."""
    planning_state_payload = context.metadata.get("planning_state")
    if isinstance(planning_state_payload, dict):
        planning_state = PlanningState.model_validate(planning_state_payload)
    else:
        planning_state = planning_state_init(context.run_id)
    planning_updated_by_tool = False
    for envelope in result.envelopes:
        if envelope.call.tool_name not in PLANNING_TOOL_NAMES:
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        planning_updated_by_tool = True
        planning_state = apply_planning_state_tool_update(
            planning_state, structured.get("applied_args", {})
        )
        if isinstance(structured.get("planning_step"), dict):
            context.metadata["planning_step"] = structured["planning_step"]
    context.metadata["planning_state"] = planning_state.model_dump(mode="json")
    return planning_updated_by_tool


def update_planning_state_from_tool_results(context: RunContext) -> None:
    """Update minimal planning state and latest planning step payload."""
    tool_results = context.metadata.get("tool_results", [])
    if not isinstance(tool_results, list):
        tool_results = []
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
    planning_state_payload = context.metadata.get("planning_state")
    if isinstance(planning_state_payload, dict):
        state = planning_state_set_step(
            PlanningState.model_validate(planning_state_payload), planning_step
        )
    else:
        state = planning_state_set_step(
            planning_state_init(context.run_id), planning_step
        )
    context.metadata["planning_step"] = planning_step.model_dump(mode="json")
    context.metadata["planning_state"] = state.model_dump(mode="json")


__all__ = [
    "PLANNING_TOOL_NAMES",
    "apply_planning_updates_from_envelopes",
    "update_planning_state_from_tool_results",
]
