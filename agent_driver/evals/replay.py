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
    for event in sorted(output.events, key=lambda item: item.seq):
        lines.append(f"[{event.seq}] {event.type.value} payload={event.payload}")
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
        "checkpoint": (
            output.checkpoint.model_dump(mode="json") if output.checkpoint else None
        ),
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }
