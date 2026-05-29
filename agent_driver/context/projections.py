"""Context projection builders for runtime outputs and replay."""

from __future__ import annotations

from typing import Any

from agent_driver.context.projection_input import MemoryProjectionInput
from agent_driver.contracts.enums import MemoryProjectionView, MemoryStepKind
from agent_driver.contracts.memory import MemoryProjection, MemoryStep


def build_memory_projection(inp: MemoryProjectionInput) -> MemoryProjection:
    """Build succinct memory projection from runtime metadata."""
    steps: list[MemoryStep] = []
    if inp.answer:
        steps.append(
            MemoryStep(
                step_index=1,
                kind=MemoryStepKind.FINAL_ANSWER,
                title="Final answer",
                content=inp.answer,
            )
        )
    if inp.observations:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.ACTION,
                title="Observations",
                payload={"observations": list(inp.observations)},
                metadata={"count": len(inp.observations)},
            )
        )
    if inp.planning_state:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.PLANNING,
                title="Planning state",
                payload=inp.planning_state,
            )
        )
    if inp.prompt_render:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.SYSTEM_PROMPT,
                title="Prompt render",
                payload={
                    "template_id": inp.prompt_render.get("template_id"),
                    "template_version": inp.prompt_render.get("template_version"),
                    "rendered_hash": inp.prompt_render.get("rendered_hash"),
                },
            )
        )
    summarized_results = _summarize_tool_results(inp.tool_results)
    if summarized_results:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.ACTION,
                title="Tool results",
                payload={"results": summarized_results},
                metadata={"count": len(summarized_results)},
            )
        )
    extracted_groups = [
        item
        for item in inp.subagent_groups
        if isinstance(item.get("group_id"), str)
        and isinstance(item.get("join_policy"), str)
    ]
    if extracted_groups:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.SUBAGENT,
                title="Subagent groups",
                payload={
                    "groups": [
                        {
                            "group_id": row.get("group_id"),
                            "join_policy": row.get("join_policy"),
                            "status": row.get("status"),
                        }
                        for row in extracted_groups[:10]
                    ]
                },
                metadata={"count": len(extracted_groups)},
            )
        )
    trim_metadata = inp.trim_metadata or {}
    return MemoryProjection(
        run_id=inp.run_id,
        attempt_id=inp.attempt_id,
        view=MemoryProjectionView.SUCCINCT,
        steps=steps,
        metadata={
            "trim": trim_metadata,
            "artifact_refs": list(inp.artifact_refs),
            "digest_refs": list(inp.digest_refs),
            "prompt_render": (
                {
                    "template_id": inp.prompt_render.get("template_id"),
                    "template_version": inp.prompt_render.get("template_version"),
                    "rendered_hash": inp.prompt_render.get("rendered_hash"),
                }
                if isinstance(inp.prompt_render, dict)
                else None
            ),
            "tool_results_count": len(summarized_results),
            "subagent_group_count": len(extracted_groups),
            "subagent_run_count": len(inp.subagent_runs),
        },
    )


def _summarize_tool_results(
    tool_results: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for item in tool_results:
        summary = item.get("summary")
        tool_name = item.get("call", {}).get("tool_name")
        if isinstance(summary, str):
            summarized.append({"tool_name": tool_name, "summary": summary[:160]})
    return summarized


__all__ = ["build_memory_projection"]
