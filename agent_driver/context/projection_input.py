"""Input bundle for memory projection assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryProjectionInput:
    """Inputs for succinct memory projection."""

    run_id: str
    attempt_id: str
    answer: str | None
    observations: tuple[dict[str, Any], ...] = ()
    planning_state: dict[str, Any] | None = None
    trim_metadata: dict[str, Any] | None = None
    artifact_refs: tuple[dict[str, Any], ...] = ()
    digest_refs: tuple[dict[str, Any], ...] = ()
    prompt_render: dict[str, Any] | None = None
    tool_results: tuple[dict[str, Any], ...] = ()
    subagent_groups: tuple[dict[str, Any], ...] = ()
    subagent_runs: tuple[dict[str, Any], ...] = ()


__all__ = ["MemoryProjectionInput"]
