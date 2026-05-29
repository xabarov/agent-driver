"""Coordinator/worker role definitions for multi-agent runs."""

from __future__ import annotations

from dataclasses import dataclass

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
        allowed_tools=("web_search", "web_fetch", "grep_search", "read_file"),
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


__all__ = [
    "DEFAULT_WORKER_DEFINITIONS",
    "WorkerDefinition",
    "default_worker_definitions",
    "worker_definition_by_type",
]
