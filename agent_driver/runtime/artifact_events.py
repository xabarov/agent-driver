"""Runtime artifact event projection from filesystem tool results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.tools import ToolResultEnvelope

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext


_ARTIFACT_WRITE_TOOLS = {"file_write", "file_edit", "file_patch", "notebook_edit"}


def artifact_event_from_tool_result(
    context: "RunContext",
    envelope: ToolResultEnvelope,
) -> tuple[RuntimeEventType, dict[str, object]] | None:
    """Build artifact event payload for successful workspace file writes."""
    if envelope.call.tool_name not in _ARTIFACT_WRITE_TOOLS:
        return None
    structured = envelope.structured_output
    if not isinstance(structured, dict):
        return None
    if structured.get("dry_run") is True:
        return None
    path = _workspace_relative_path(context, structured.get("path"))
    if path is None:
        return None
    operation = str(structured.get("operation") or envelope.call.tool_name)
    size_bytes = _as_non_negative_int(structured.get("size_bytes"))
    payload: dict[str, object] = {
        "path": path,
        "kind": _artifact_kind(path),
        "operation": operation,
        "tool_name": envelope.call.tool_name,
        "tool_call_id": envelope.call.tool_call_id,
    }
    if size_bytes is not None:
        payload["size_bytes"] = size_bytes
        payload["bytes"] = size_bytes
    if path == "research/sources.jsonl":
        record_count = _source_ledger_record_count(envelope.call.args.get("content"))
        if record_count is not None:
            payload["record_count"] = record_count
    replacements = _as_non_negative_int(structured.get("replacements"))
    if replacements is not None:
        payload["replacements"] = replacements
    mode = structured.get("mode")
    if isinstance(mode, str) and mode:
        payload["mode"] = mode
    event_type = (
        RuntimeEventType.ARTIFACT_CREATED
        if structured.get("created") is True
        else RuntimeEventType.ARTIFACT_UPDATED
    )
    return event_type, payload


def _workspace_relative_path(context: "RunContext", raw_path: object) -> str | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    workspace = context.metadata.get("workspace_cwd")
    if not isinstance(workspace, str) or not workspace.strip():
        return None
    root = Path(workspace).expanduser().resolve()
    path = Path(raw_path).expanduser().resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _artifact_kind(path: str) -> str:
    if path == "research/report.md":
        return "report"
    if path.startswith("research/"):
        return "research"
    if path.startswith("tool-results/"):
        return "tool_result"
    return "file"


def _as_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return None


def _source_ledger_record_count(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    return len([line for line in value.splitlines() if line.strip()])


__all__ = ["artifact_event_from_tool_result"]
