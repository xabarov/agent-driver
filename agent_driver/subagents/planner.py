"""Subagent context handoff planning helpers."""

from __future__ import annotations

from typing import Any

from agent_driver.subagents.specs import SubagentTaskSpec
from agent_driver.subagents.workers import worker_definition_for_metadata


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
    worker_definition = worker_definition_for_metadata(task.metadata)
    scratchpad_policy = _dict_metadata(task.metadata.get("scratchpad"))
    artifact_policy = _dict_metadata(task.metadata.get("artifact_handoff"))
    handoff = {
        "task_id": task.task_id,
        "task": task.task,
        "description": task.description,
        "parent_summary": parent_summary[:1200],
        "artifact_refs": bounded_artifacts,
        "digest_refs": bounded_digests,
        "planning_state": planning_state or {},
        "context_refs": list(task.context_refs),
        "scratchpad": {
            "mode": str(scratchpad_policy.get("mode") or "bounded_private"),
            "max_chars": int(scratchpad_policy.get("max_chars") or 4000),
            "share_with_parent": bool(
                scratchpad_policy.get("share_with_parent", False)
            ),
        },
        "artifact_handoff": {
            "mode": str(artifact_policy.get("mode") or "refs_only"),
            "max_refs": int(artifact_policy.get("max_refs") or max_refs),
            "required_outputs": list(
                artifact_policy.get("required_outputs")
                or task.metadata.get("required_outputs")
                or []
            ),
        },
    }
    if worker_definition is not None:
        handoff["worker"] = {
            "type": worker_definition.worker_type,
            "display_name": worker_definition.display_name,
            "purpose": worker_definition.purpose,
            "allowed_tools": list(worker_definition.allowed_tools),
            "handoff_rules": list(worker_definition.handoff_rules),
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


def _dict_metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["build_child_context_handoff"]
