"""Replay/debug projections and redaction-safe support bundles."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.observability import build_trace_export


def render_full_debug_view(output: AgentRunOutput) -> dict[str, Any]:
    """Render full debug payload for one run output."""
    return {
        "run_id": output.run_id,
        "attempt_id": output.attempt_id,
        "status": output.status.value,
        "terminal_reason": (
            output.terminal_reason.value if output.terminal_reason else None
        ),
        "events": [event.model_dump(mode="json") for event in output.events],
        "tool_trace": [trace.model_dump(mode="json") for trace in output.tool_trace],
        "usage": output.usage.model_dump(mode="json") if output.usage else None,
        "checkpoint": (
            output.checkpoint.model_dump(mode="json") if output.checkpoint else None
        ),
        "metadata": output.metadata,
    }


def render_succinct_view(output: AgentRunOutput) -> dict[str, Any]:
    """Render compact summary for operator/debug quick checks."""
    event_types = [event.type.value for event in output.events]
    token_pressure = output.metadata.get("token_pressure")
    trim_audit = output.metadata.get("trim_audit")
    micro_audit = output.metadata.get("microcompaction_audit")
    planning_state = output.metadata.get("planning_state")
    return {
        "run_id": output.run_id,
        "status": output.status.value,
        "terminal_reason": (
            output.terminal_reason.value if output.terminal_reason else None
        ),
        "event_count": len(output.events),
        "event_types": event_types,
        "tool_calls": len(output.tool_trace),
        "warnings": len(output.warnings),
        "planning_event_count": len(
            [
                event
                for event in output.events
                if event.payload.get("channel") == "planning"
            ]
        ),
        "token_pressure": token_pressure if isinstance(token_pressure, dict) else None,
        "trim_audit_size": len(trim_audit) if isinstance(trim_audit, list) else 0,
        "microcompaction_audit_size": (
            len(micro_audit) if isinstance(micro_audit, list) else 0
        ),
        "has_planning_state": isinstance(planning_state, dict),
        "subagent_group_count": len(output.subagent_groups),
        "subagent_run_count": len(output.subagent_runs),
    }


def render_cli_replay(output: AgentRunOutput) -> str:
    """Render human-readable deterministic CLI replay transcript."""
    lines = [
        f"run={output.run_id}",
        f"attempt={output.attempt_id}",
        f"status={output.status.value}",
    ]
    if output.terminal_reason is not None:
        lines.append(f"terminal_reason={output.terminal_reason.value}")
    if output.memory_projection is not None:
        lines.append(
            "memory_projection="
            f"{output.memory_projection.view.value} "
            f"steps={len(output.memory_projection.steps)}"
        )
        projection_meta = output.memory_projection.metadata
        if isinstance(projection_meta, dict):
            prompt_meta = projection_meta.get("prompt_render")
            if isinstance(prompt_meta, dict):
                lines.append(
                    "prompt_render="
                    f"{prompt_meta.get('template_id')}#"
                    f"{prompt_meta.get('template_version')} "
                    f"hash={prompt_meta.get('rendered_hash')}"
                )
            tool_results_count = projection_meta.get("tool_results_count")
            if isinstance(tool_results_count, int):
                lines.append(f"tool_results_count={tool_results_count}")
    if output.subagent_groups:
        lines.append(f"subagent_groups={len(output.subagent_groups)}")
    if output.subagent_runs:
        lines.append(f"subagent_runs={len(output.subagent_runs)}")
    for event in sorted(output.events, key=lambda item: item.seq):
        lines.append(f"[{event.seq}] {event.type.value} payload={event.payload}")
    token_pressure = output.metadata.get("token_pressure")
    if isinstance(token_pressure, dict):
        lines.append(f"token_pressure={token_pressure}")
    trim_audit = output.metadata.get("trim_audit")
    if isinstance(trim_audit, list):
        lines.append(f"trim_audit_size={len(trim_audit)}")
    micro_audit = output.metadata.get("microcompaction_audit")
    if isinstance(micro_audit, list):
        lines.append(f"microcompaction_audit_size={len(micro_audit)}")
    return "\n".join(lines)


def build_support_bundle(output: AgentRunOutput) -> dict[str, Any]:
    """Build redaction-safe support bundle from one run output."""
    trace_export = build_trace_export(output)
    return {
        "run": {
            "run_id": output.run_id,
            "attempt_id": output.attempt_id,
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
        },
        "trace": trace_export.model_dump(mode="json"),
        "warnings": [warning.model_dump(mode="json") for warning in output.warnings],
        "tool_trace": [trace.model_dump(mode="json") for trace in output.tool_trace],
        "subagent_groups": [group.model_dump(mode="json") for group in output.subagent_groups],
        "subagent_runs": [row.model_dump(mode="json") for row in output.subagent_runs],
        "checkpoint": (
            output.checkpoint.model_dump(mode="json") if output.checkpoint else None
        ),
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }
