"""Pure helpers for assembling AgentRunOutput fields."""

from __future__ import annotations

from typing import Any

from agent_driver.context import (
    build_memory_projection,
)
from agent_driver.context.projection_input import MemoryProjectionInput
from agent_driver.contracts.tools import ToolTrace
from agent_driver.runtime.metadata_state import CompactionRuntimeState, ToolLoopState
from agent_driver.runtime.single_agent.types import RunContext


def collect_tool_trace(context: RunContext) -> list[ToolTrace]:
    """Parse tool traces from context metadata."""
    return [
        ToolTrace.model_validate(item)
        for item in ToolLoopState(context.metadata).tool_trace()
    ]


def list_dict_metadata(context: RunContext, key: str) -> list[dict[str, Any]]:
    payload = context.metadata.get(key, [])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def dict_metadata(context: RunContext, key: str) -> dict[str, Any] | None:
    payload = context.metadata.get(key)
    return payload if isinstance(payload, dict) else None


def build_memory_projection_for_context(
    context: RunContext,
    *,
    answer: str | None,
    normalized_tool_results: list[dict[str, Any]],
    artifact_refs: list[dict[str, Any]],
    digest_refs: list[dict[str, Any]],
) -> Any:
    """Build memory projection from normalized context slices."""
    return build_memory_projection(
        MemoryProjectionInput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            answer=answer,
            observations=tuple(list_dict_metadata(context, "observations")),
            planning_state=dict_metadata(context, "planning_state"),
            trim_metadata=dict_metadata(context, "trim_metadata") or {},
            artifact_refs=tuple(artifact_refs),
            digest_refs=tuple(digest_refs),
            prompt_render=dict_metadata(context, "prompt_render"),
            tool_results=tuple(normalized_tool_results),
            subagent_groups=tuple(list_dict_metadata(context, "subagent_groups")),
            subagent_runs=tuple(list_dict_metadata(context, "subagent_runs")),
        )
    )


def build_memory_audit(context: RunContext) -> dict[str, Any]:
    """Assemble memory audit block for terminal output."""
    return CompactionRuntimeState(context.metadata).memory_audit()


__all__ = [
    "build_memory_audit",
    "build_memory_projection_for_context",
    "collect_tool_trace",
    "dict_metadata",
    "list_dict_metadata",
]
