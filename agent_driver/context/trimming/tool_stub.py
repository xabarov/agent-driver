"""Shared wording for tool-payload trim stubs."""

from __future__ import annotations


def build_tool_trim_stub_content(
    *,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> str:
    """Return model-facing text for a trimmed raw tool payload."""
    name = (tool_name or "tool").strip() or "tool"
    call_id = (tool_call_id or "").strip()
    suffix = f" id={call_id}" if call_id else ""
    return (
        f"[trimmed] Full raw payload for {name}{suffix} was shortened to fit the "
        "context budget. Use retained Observations, artifact references, and "
        "provenance metadata as sourced evidence; re-run the tool only if exact "
        "raw values are needed."
    )
