"""Coordinator/worker role definitions for multi-agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent_driver.contracts.enums import AgentProfile


@dataclass(frozen=True, slots=True)
class WorkerDefinition:
    """Static worker role definition used by coordinator prompts/config."""

    worker_type: str
    display_name: str
    profile: AgentProfile
    purpose: str
    allowed_tools: tuple[str, ...]
    handoff_rules: tuple[str, ...]


DEFAULT_WORKER_DEFINITIONS: tuple[WorkerDefinition, ...] = (
    WorkerDefinition(
        worker_type="worker",
        display_name="Worker",
        profile=AgentProfile.REACT_TEXT,
        purpose="General delegated analysis or execution task.",
        allowed_tools=("read_file", "grep_search", "glob_search", "web_search"),
        handoff_rules=(
            "Return concise findings with evidence pointers.",
            "Ask for continuation through the mailbox when blocked.",
        ),
    ),
    WorkerDefinition(
        worker_type="researcher",
        display_name="Researcher",
        profile=AgentProfile.REACT_TEXT,
        purpose="Independent research, comparison, and source gathering.",
        allowed_tools=(
            "todo_write",
            "web_search",
            "web_fetch",
            "grep_search",
            "read_file",
        ),
        handoff_rules=(
            "Cite concrete URLs or file paths for every important claim.",
            "Separate facts from interpretation.",
        ),
    ),
    WorkerDefinition(
        worker_type="implementer",
        display_name="Implementer",
        profile=AgentProfile.REACT_TEXT,
        purpose="Scoped implementation work after coordinator-approved direction.",
        allowed_tools=("read_file", "grep_search", "glob_search", "python"),
        handoff_rules=(
            "Keep edits scoped to the assigned task.",
            "Report changed files and verification commands.",
        ),
    ),
    WorkerDefinition(
        worker_type="verifier",
        display_name="Verifier",
        profile=AgentProfile.REACT_TEXT,
        purpose="Independent verification, regression checks, and critique.",
        allowed_tools=("read_file", "grep_search", "glob_search", "python"),
        handoff_rules=(
            "Prioritize bugs, missed tests, and behavioral regressions.",
            "Do not rubber-stamp; report residual risk explicitly.",
        ),
    ),
)


def default_worker_definitions() -> tuple[WorkerDefinition, ...]:
    """Return built-in coordinator worker definitions."""
    return DEFAULT_WORKER_DEFINITIONS


def worker_definition_by_type(worker_type: str) -> WorkerDefinition | None:
    """Return a built-in worker definition by stable worker type."""
    normalized = worker_type.strip().lower()
    for definition in DEFAULT_WORKER_DEFINITIONS:
        if definition.worker_type == normalized:
            return definition
    return None


def worker_definition_for_metadata(
    metadata: Mapping[str, Any] | None,
) -> WorkerDefinition | None:
    """Resolve a worker definition from task metadata."""
    if not metadata:
        return None
    worker_type = metadata.get("worker_type") or metadata.get("role")
    if worker_type is None:
        return None
    return worker_definition_by_type(str(worker_type))


def apply_worker_tool_surface(
    *,
    parent_tool_policy: Mapping[str, Any],
    worker_type: str | None,
) -> dict[str, Any]:
    """Return a child tool policy narrowed by worker role definition."""
    policy = dict(parent_tool_policy)
    if not worker_type:
        return policy
    definition = worker_definition_by_type(worker_type)
    if definition is None:
        return policy

    worker_allowed = list(definition.allowed_tools)
    parent_allowed = policy.get("allowed_tools")
    if isinstance(parent_allowed, list):
        parent_allowed_set = {str(item) for item in parent_allowed}
        allowed_tools = [tool for tool in worker_allowed if tool in parent_allowed_set]
    else:
        allowed_tools = worker_allowed

    denied = policy.get("denied_tools")
    if isinstance(denied, list) and denied:
        denied_set = {str(item) for item in denied}
        allowed_tools = [tool for tool in allowed_tools if tool not in denied_set]

    metadata = dict(policy.get("metadata") or {})
    metadata["worker_type"] = definition.worker_type
    metadata["worker_allowed_tools"] = worker_allowed
    metadata["worker_tool_surface"] = "role_restricted"
    return {
        **policy,
        "allowed_tools": allowed_tools,
        "metadata": metadata,
    }


__all__ = [
    "DEFAULT_WORKER_DEFINITIONS",
    "WorkerDefinition",
    "apply_worker_tool_surface",
    "default_worker_definitions",
    "worker_definition_for_metadata",
    "worker_definition_by_type",
]
