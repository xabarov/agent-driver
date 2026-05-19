"""Planning-step prompt renderer for optional model guidance."""

from __future__ import annotations

from agent_driver.contracts.context import PlanningStep


def render_planning_step_prompt(step: PlanningStep) -> str:
    """Render deterministic planning-step prompt block."""
    sections: list[str] = ["Planning Update", "", "Facts Given:"]
    sections.extend(
        [f"- {item}" for item in step.facts_given] if step.facts_given else ["- (none)"]
    )
    sections.extend(["", "Facts Learned:"])
    sections.extend(
        [f"- {item}" for item in step.facts_learned]
        if step.facts_learned
        else ["- (none)"]
    )
    sections.extend(["", "Facts To Look Up:"])
    sections.extend(
        [f"- {item}" for item in step.facts_to_lookup]
        if step.facts_to_lookup
        else ["- (none)"]
    )
    sections.extend(["", "Facts To Derive:"])
    sections.extend(
        [f"- {item}" for item in step.facts_to_derive]
        if step.facts_to_derive
        else ["- (none)"]
    )
    sections.extend(["", "Next Plan:", step.next_plan])
    return "\n".join(sections)
