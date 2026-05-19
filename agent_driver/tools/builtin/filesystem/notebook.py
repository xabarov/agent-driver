"""Notebook edit builtin tool."""

from __future__ import annotations

import json
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._paths import (
    MAX_BYTES_DEFAULT,
    as_int,
    read_text_with_size_guard,
    resolve_file_path,
)

NOTEBOOK_EDIT_TOOL = "notebook_edit"
ALLOWED_CELL_TYPES = {"code", "markdown", "raw"}


def notebook_edit_manifest() -> ToolManifest:
    """Build notebook_edit manifest."""
    return ToolManifest(
        name=NOTEBOOK_EDIT_TOOL,
        description=(
            "Edit one notebook cell deterministically by inserting a new cell or "
            "replacing old_text with new_text exactly once."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=15.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute .ipynb file path"},
                "cell_idx": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Zero-based notebook cell index",
                },
                "is_new_cell": {
                    "type": "boolean",
                    "description": "Insert new cell when true; edit existing when false",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown", "raw"],
                    "description": "Type for inserted cell, defaults to code",
                },
                "old_text": {
                    "type": "string",
                    "description": "Text to replace in existing cell (ignored for inserts)",
                },
                "new_text": {"type": "string", "description": "Replacement/new cell content"},
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1_000_000,
                    "description": "Maximum notebook size when loading and saving",
                },
            },
            "required": ["path", "cell_idx", "is_new_cell", "old_text", "new_text"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def notebook_edit_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Insert or edit one notebook cell deterministically."""
    path = resolve_file_path(args.get("path"))
    max_bytes = as_int(args.get("max_bytes"), default=MAX_BYTES_DEFAULT, minimum=1)
    payload = _load_notebook(path=path, max_bytes=max_bytes)
    cells = payload.get("cells")
    if not isinstance(cells, list):
        raise ValueError("notebook payload missing 'cells' list")

    cell_idx = as_int(args.get("cell_idx"), default=0, minimum=0)
    is_new_cell = bool(args.get("is_new_cell"))
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not isinstance(old_text, str):
        raise ValueError("old_text must be a string")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be a string")

    if is_new_cell:
        if cell_idx > len(cells):
            raise ValueError(f"cell_idx out of range for insert: {cell_idx}>{len(cells)}")
        cell_type = str(args.get("cell_type") or "code").strip().lower()
        if cell_type not in ALLOWED_CELL_TYPES:
            raise ValueError("cell_type must be one of: code, markdown, raw")
        cells.insert(cell_idx, _new_cell(cell_type=cell_type, source_text=new_text))
        operation = "insert"
    else:
        if cell_idx >= len(cells):
            raise ValueError(f"cell_idx out of range for replace: {cell_idx}>={len(cells)}")
        operation = "replace"
        _replace_in_cell(cells[cell_idx], old_text=old_text, new_text=new_text)

    rendered = json.dumps(payload, indent=1) + "\n"
    size_bytes = len(rendered.encode("utf-8"))
    if size_bytes > max_bytes:
        raise ValueError(f"content exceeds max_bytes ({size_bytes}>{max_bytes})")
    path.write_text(rendered, encoding="utf-8")
    return {
        "summary": f"{operation} notebook cell in {path}",
        "path": str(path),
        "operation": operation,
        "cell_idx": cell_idx,
        "size_bytes": size_bytes,
    }


def _load_notebook(*, path, max_bytes: int) -> dict[str, Any]:
    raw = read_text_with_size_guard(path, max_bytes=max_bytes)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid notebook json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("notebook payload must be a JSON object")
    return payload


def _new_cell(*, cell_type: str, source_text: str) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": [source_text],
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _replace_in_cell(cell: Any, *, old_text: str, new_text: str) -> None:
    if not isinstance(cell, dict):
        raise ValueError("target cell must be an object")
    source = cell.get("source")
    if isinstance(source, list):
        source_text = "".join(str(item) for item in source)
    elif isinstance(source, str):
        source_text = source
    else:
        raise ValueError("cell source must be string or list of strings")
    if old_text == "":
        raise ValueError("old_text must be non-empty for replace")
    occurrences = source_text.count(old_text)
    if occurrences != 1:
        raise ValueError("old_text must appear exactly once in target cell")
    updated = source_text.replace(old_text, new_text, 1)
    cell["source"] = [updated]


__all__ = ["notebook_edit_handler", "notebook_edit_manifest"]
