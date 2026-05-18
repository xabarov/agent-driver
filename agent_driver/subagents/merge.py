"""Subagent merge strategies with provenance-preserving summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import ParentStateWriteMode, SubagentMergeMode
from agent_driver.contracts.subagents import MergeProvenance, SubagentRun


def merge_subagent_outputs(
    *,
    merge_mode: SubagentMergeMode,
    runs: list[SubagentRun],
) -> tuple[str, MergeProvenance]:
    """Merge child outputs into bounded parent summary text."""
    completed = [item for item in runs if item.status.value == "completed"]
    if not completed:
        return (
            "No successful child outputs.",
            MergeProvenance(
                strategy=merge_mode.value,
                source_kind="subagent_runs",
                carried_keys=[],
                parent_state_write=ParentStateWriteMode.NONE,
                metadata={"completed": 0},
            ),
        )
    snippets: list[str] = []
    for row in completed:
        note = row.metadata.get("summary") if isinstance(row.metadata, dict) else None
        snippets.append(str(note or f"child {row.subagent_run_id} completed"))
    if merge_mode == SubagentMergeMode.RANK:
        snippets = sorted(snippets, key=len, reverse=True)
    if merge_mode == SubagentMergeMode.VOTE:
        winner = max(set(snippets), key=snippets.count)
        snippets = [winner]
    if merge_mode == SubagentMergeMode.MANUAL:
        snippets = [f"manual review required: {len(snippets)} candidates"]
    merged = "\n".join(snippets[:5])[:2000]
    provenance = MergeProvenance(
        strategy=merge_mode.value,
        source_kind="subagent_runs",
        carried_keys=["summary"],
        parent_state_write=ParentStateWriteMode.BOUNDED_APPEND_ONLY,
        metadata={
            "completed": len(completed),
            "mode": merge_mode.value,
            "discarded_count": max(0, len(snippets) - 5),
        },
    )
    return merged, provenance


def summarize_child_runs_for_parent(runs: list[Any]) -> list[dict[str, Any]]:
    """Build bounded child summary rows for parent metadata/replay."""
    rows: list[dict[str, Any]] = []
    for raw in runs[:20]:
        if isinstance(raw, SubagentRun):
            item = raw
        elif isinstance(raw, dict):
            rows.append(
                {
                    "subagent_run_id": raw.get("subagent_run_id"),
                    "status": raw.get("status"),
                    "terminal_state": raw.get("terminal_state"),
                    "latency_ms": raw.get("latency_ms"),
                    "task_id": raw.get("task_id"),
                }
            )
            continue
        else:
            continue
        rows.append(
            {
                "subagent_run_id": item.subagent_run_id,
                "status": item.status.value,
                "terminal_state": (
                    item.terminal_state.value if item.terminal_state else None
                ),
                "latency_ms": item.latency_ms,
                "task_id": item.task_id,
            }
        )
    return rows


__all__ = ["merge_subagent_outputs", "summarize_child_runs_for_parent"]
