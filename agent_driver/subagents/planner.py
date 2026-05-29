"""Subagent context handoff planning helpers."""

from __future__ import annotations

from typing import Any

from agent_driver.subagents.specs import SubagentTaskSpec


def build_child_context_handoff(
    *,
    task: SubagentTaskSpec,
    parent_summary: str,
    artifact_refs: list[dict[str, Any]],
    digest_refs: list[dict[str, Any]],
    planning_state: dict[str, Any] | None,
    max_refs: int = 8,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build bounded child input context and handoff audit payload."""
    bounded_artifacts = artifact_refs[:max_refs]
    bounded_digests = digest_refs[:max_refs]
    handoff = {
        "task_id": task.task_id,
        "task": task.task,
        "description": task.description,
        "parent_summary": parent_summary[:1200],
        "artifact_refs": bounded_artifacts,
        "digest_refs": bounded_digests,
        "planning_state": planning_state or {},
        "context_refs": list(task.context_refs),
    }
    audit = {
        "artifact_refs_in": len(artifact_refs),
        "artifact_refs_kept": len(bounded_artifacts),
        "digest_refs_in": len(digest_refs),
        "digest_refs_kept": len(bounded_digests),
        "dropped_artifacts": max(0, len(artifact_refs) - len(bounded_artifacts)),
        "dropped_digests": max(0, len(digest_refs) - len(bounded_digests)),
    }
    return handoff, audit


__all__ = ["build_child_context_handoff"]
