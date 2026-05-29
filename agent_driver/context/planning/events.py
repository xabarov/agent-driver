"""Planning-step event helpers (separate from ordinary runtime events)."""

from __future__ import annotations

from agent_driver.contracts.context import PlanningState, PlanningStep
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import RuntimeEvent, new_runtime_event


def planning_step_event(
    *,
    run_id: str,
    attempt_id: str,
    seq: int,
    step: PlanningStep,
) -> RuntimeEvent:
    """Build dedicated planning-step runtime event payload."""
    return new_runtime_event(
        event_type=RuntimeEventType.NODE_COMPLETED,
        context={"run_id": run_id, "attempt_id": attempt_id, "seq": seq},
        options={
            "node_id": "planning_step",
            "payload": {
                "planning_step_id": step.step_id,
                "facts_given": step.facts_given,
                "facts_learned": step.facts_learned,
                "facts_to_lookup": step.facts_to_lookup,
                "facts_to_derive": step.facts_to_derive,
                "next_plan": step.next_plan,
                "channel": "planning",
            },
        },
    )


def planning_state_event(
    *,
    run_id: str,
    attempt_id: str,
    seq: int,
    state: PlanningState,
) -> RuntimeEvent:
    """Build dedicated planning-state event payload."""
    return new_runtime_event(
        event_type=RuntimeEventType.NODE_COMPLETED,
        context={"run_id": run_id, "attempt_id": attempt_id, "seq": seq},
        options={
            "node_id": "planning_state",
            "payload": {
                "todos": [item.model_dump(mode="json") for item in state.todos],
                "latest_step_id": (
                    state.latest_step.step_id if state.latest_step else None
                ),
                "channel": "planning",
            },
        },
    )
