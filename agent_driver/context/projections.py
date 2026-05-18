"""Context projection builders for runtime outputs and replay."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import MemoryProjectionView, MemoryStepKind
from agent_driver.contracts.memory import MemoryProjection, MemoryStep


def build_memory_projection(  # pylint: disable=too-many-arguments
    *,
    run_id: str,
    attempt_id: str,
    answer: str | None,
    observations: list[dict[str, Any]],
    planning_state: dict[str, Any] | None,
    trim_metadata: dict[str, Any],
    artifact_refs: list[dict[str, Any]],
    digest_refs: list[dict[str, Any]],
    prompt_render: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> MemoryProjection:
    """Build succinct memory projection from runtime metadata."""
    steps: list[MemoryStep] = []
    if answer:
        steps.append(
            MemoryStep(
                step_index=1,
                kind=MemoryStepKind.FINAL_ANSWER,
                title="Final answer",
                content=answer,
            )
        )
    if observations:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.ACTION,
                title="Observations",
                payload={"observations": observations},
                metadata={"count": len(observations)},
            )
        )
    if planning_state:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.PLANNING,
                title="Planning state",
                payload=planning_state,
            )
        )
    if prompt_render:
        steps.append(
            MemoryStep(
                step_index=len(steps) + 1,
                kind=MemoryStepKind.SYSTEM_PROMPT,
                title="Prompt render",
                payload={
                    "template_id": prompt_render.get("template_id"),
                    "template_version": prompt_render.get("template_version"),
                    "rendered_hash": prompt_render.get("rendered_hash"),
                },
            )
        )
    summarized_results = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        tool_name = item.get("call", {}).get("tool_name")
        if isinstance(summary, str):
            summarized_results.append(
                {"tool_name": tool_name, "summary": summary[:160]}
            )
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
    return MemoryProjection(
        run_id=run_id,
        attempt_id=attempt_id,
        view=MemoryProjectionView.SUCCINCT,
        steps=steps,
        metadata={
            "trim": trim_metadata,
            "artifact_refs": artifact_refs,
            "digest_refs": digest_refs,
            "prompt_render": (
                {
                    "template_id": prompt_render.get("template_id"),
                    "template_version": prompt_render.get("template_version"),
                    "rendered_hash": prompt_render.get("rendered_hash"),
                }
                if isinstance(prompt_render, dict)
                else None
            ),
            "tool_results_count": len(summarized_results),
        },
    )


__all__ = ["build_memory_projection"]
