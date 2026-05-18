"""Parent context handoff for subagent group execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubagentParentHandoff:
    """Parent context passed into subagent group execution."""

    run_id: str
    attempt_id: str
    thread_id: str | None
    agent_id: str
    graph_preset: str
    model_role: str
    tool_policy: dict[str, object]
    answer: str | None
    artifact_refs: list[dict[str, object]]
    digest_refs: list[dict[str, object]]
    planning_state: dict[str, object] | None


def parent_handoff_from_legacy_kwargs(
    *,
    parent_run_id: str,
    parent_attempt_id: str,
    parent_thread_id: str | None,
    parent_agent_id: str,
    parent_graph_preset: str,
    parent_model_role: str,
    parent_tool_policy: dict[str, object],
    parent_answer: str | None,
    parent_artifact_refs: list[dict[str, object]],
    parent_digest_refs: list[dict[str, object]],
    parent_planning_state: dict[str, object] | None,
) -> SubagentParentHandoff:
    """Map legacy executor keyword names to SubagentParentHandoff."""
    return SubagentParentHandoff(
        run_id=parent_run_id,
        attempt_id=parent_attempt_id,
        thread_id=parent_thread_id,
        agent_id=parent_agent_id,
        graph_preset=parent_graph_preset,
        model_role=parent_model_role,
        tool_policy=parent_tool_policy,
        answer=parent_answer,
        artifact_refs=parent_artifact_refs,
        digest_refs=parent_digest_refs,
        planning_state=parent_planning_state,
    )


__all__ = ["SubagentParentHandoff", "parent_handoff_from_legacy_kwargs"]
