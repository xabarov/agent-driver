"""Generic helpers for detecting real tool execution evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_TOOL_COMPLETED_EVENT = "tool_call_completed"
_NON_EXECUTION_STATUSES = frozenset({"blocked", "denied", "skipped", "policy_denied"})


@dataclass(frozen=True)
class ExecutionProof:
    """Summary of tool execution evidence found in runtime stream/log events."""

    real_execution_proof: bool
    completed_tool_names: tuple[str, ...] = ()
    completed_tool_count: int = 0


def _event_name(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("event") or event.get("type") or "").strip()
    return str(getattr(event, "event", "") or "").strip()


def _event_data(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        data = event.get("data") or event.get("payload") or {}
    else:
        data = getattr(event, "data", None) or getattr(event, "payload", None) or {}
    return data if isinstance(data, dict) else {}


def _tool_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    tools = data.get("tools")
    if isinstance(tools, list):
        rows = [tool for tool in tools if isinstance(tool, dict)]
        if rows:
            return rows
    return [data]


def summarize_execution_proof(events: Iterable[Any]) -> ExecutionProof:
    """Return generic proof that a run completed at least one real tool call.

    The helper deliberately avoids host-specific policy concepts. It only
    looks for normalized ``tool_call_completed`` events with a tool name and
    a status that does not describe a non-execution outcome.
    """
    completed_tool_names: list[str] = []
    for event in events:
        if _event_name(event) != _TOOL_COMPLETED_EVENT:
            continue
        for row in _tool_rows(_event_data(event)):
            name = str(row.get("tool_name") or row.get("name") or "").strip()
            if not name:
                continue
            status = str(row.get("status") or "completed").strip().lower()
            if status in _NON_EXECUTION_STATUSES:
                continue
            completed_tool_names.append(name)
    return ExecutionProof(
        real_execution_proof=bool(completed_tool_names),
        completed_tool_names=tuple(completed_tool_names),
        completed_tool_count=len(completed_tool_names),
    )


def has_real_execution_proof(events: Iterable[Any]) -> bool:
    """Return True when normalized events include completed tool execution."""
    return summarize_execution_proof(events).real_execution_proof


__all__ = [
    "ExecutionProof",
    "has_real_execution_proof",
    "summarize_execution_proof",
]
