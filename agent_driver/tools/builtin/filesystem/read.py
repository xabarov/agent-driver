"""Read file builtin manifest and handler."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._paths import (
    MAX_BYTES_DEFAULT,
    as_int,
    as_optional_int,
    read_text_with_size_guard,
    resolve_file_path,
)

READ_FILE_TOOL = "read_file"


def read_file_manifest() -> ToolManifest:
    """Build read_file tool manifest."""
    return ToolManifest(
        name=READ_FILE_TOOL,
        description=(
            "Read a UTF-8 text file from workspace, optionally with offset and "
            "line limit; returns numbered lines."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=8000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path; absolute or relative to workspace cwd",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based line offset; negative counts from end",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return",
                    "minimum": 1,
                    "maximum": 4000,
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Safety cap for file size in bytes",
                    "minimum": 1,
                    "maximum": 1_000_000,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def read_file_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Read file and return numbered lines."""
    path = resolve_file_path(args.get("path"))
    max_bytes = as_int(args.get("max_bytes"), default=MAX_BYTES_DEFAULT, minimum=1)
    raw = read_text_with_size_guard(path, max_bytes=max_bytes)
    lines = raw.splitlines()
    sliced, start_index = slice_lines(
        lines,
        offset=as_optional_int(args.get("offset")),
        limit=as_optional_int(args.get("limit")),
    )
    numbered = [f"{idx + 1}|{value}" for idx, value in enumerate(sliced, start=start_index)]
    line_range = (
        f"{start_index + 1}-{start_index + len(sliced)}" if sliced else "empty-range"
    )
    summary = f"{path} ({len(sliced)} lines, file_lines={len(lines)}, range={line_range})"
    return {
        "summary": summary,
        "path": str(path),
        "line_count": len(lines),
        "returned_lines": len(sliced),
        "content": "\n".join(numbered),
    }


def slice_lines(
    lines: list[str],
    *,
    offset: int | None,
    limit: int | None,
) -> tuple[list[str], int]:
    """Apply deterministic line slicing by offset and limit."""
    if offset == 0:
        raise ValueError("offset must be >= 1 or negative")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be >= 1")
    if not lines:
        return [], 0
    line_count = len(lines)
    if offset is None:
        start_index = 0
    elif offset > 0:
        if offset > line_count:
            raise ValueError(f"offset exceeds line count ({line_count})")
        start_index = offset - 1
    else:
        start_index = max(line_count + offset, 0)
    if limit is None:
        end_index = line_count
    else:
        end_index = min(start_index + max(limit, 0), line_count)
    return lines[start_index:end_index], start_index
