"""Writable filesystem builtins: file_write, file_edit, and file_patch."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._edit_result import edit_output_schema
from agent_driver.tools.builtin.filesystem._paths import (
    MAX_BYTES_DEFAULT,
    as_int,
    ensure_text_size,
    read_text_with_size_guard,
    resolve_file_path,
    resolve_writable_path,
)

FILE_WRITE_TOOL = "file_write"
FILE_EDIT_TOOL = "file_edit"
FILE_PATCH_TOOL = "file_patch"
_PREVIEW_CHARS_DEFAULT = 240


def file_write_manifest() -> ToolManifest:
    """Build file_write manifest."""
    return ToolManifest(
        name=FILE_WRITE_TOOL,
        description=(
            "Write UTF-8 text to a file path with overwrite/append mode. "
            "Relative paths resolve against workspace cwd."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": ("File path; absolute or relative to workspace cwd"),
                },
                "content": {"type": "string", "description": "Text payload to write"},
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "Write mode; overwrite by default",
                },
                "create_parent": {
                    "type": "boolean",
                    "description": "Create missing parent directories when true",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1_000_000,
                    "description": "Maximum allowed resulting file size",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview change without writing to disk",
                },
                "preview_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2000,
                    "description": "Maximum chars included in before/after preview",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        output_type="json",
        output_schema=edit_output_schema(),
        metadata={
            "implementation_status": "native",
            "adapter_kind": "filesystem_write",
            "application_tags": ["filesystem", "codegen"],
        },
    )


def file_edit_manifest() -> ToolManifest:
    """Build file_edit manifest."""
    return ToolManifest(
        name=FILE_EDIT_TOOL,
        description=(
            "Edit UTF-8 text file by replacing expected old_text occurrences with "
            "new_text. Relative paths resolve against workspace cwd."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=4000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": ("File path; absolute or relative to workspace cwd"),
                },
                "old_text": {
                    "type": "string",
                    "description": "Text snippet expected in file",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text snippet",
                },
                "expected_occurrences": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Expected count of old_text occurrences",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1_000_000,
                    "description": "Maximum allowed resulting file size",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview replacements without writing to disk",
                },
                "preview_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2000,
                    "description": "Maximum chars included in before/after preview",
                },
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
        output_type="json",
        output_schema=edit_output_schema(),
        metadata={
            "implementation_status": "native",
            "adapter_kind": "filesystem_write",
            "application_tags": ["filesystem", "codegen"],
        },
    )


def file_patch_manifest() -> ToolManifest:
    """Build file_patch manifest."""
    return ToolManifest(
        name=FILE_PATCH_TOOL,
        description=(
            "Apply multiple exact text replacements to one UTF-8 file in a "
            "single call. Use for targeted report/code patches instead of "
            "rewriting whole files."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=5000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": ("File path; absolute or relative to workspace cwd"),
                },
                "patches": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "description": "Exact replacement operations.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_text": {
                                "type": "string",
                                "description": "Text snippet expected in file",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "Replacement text snippet",
                            },
                            "expected_occurrences": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 1000,
                                "description": (
                                    "Expected count of old_text occurrences"
                                ),
                            },
                        },
                        "required": ["old_text", "new_text"],
                        "additionalProperties": False,
                    },
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1_000_000,
                    "description": "Maximum allowed resulting file size",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview replacements without writing to disk",
                },
                "preview_chars": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 2000,
                    "description": "Maximum chars included in before/after preview",
                },
            },
            "required": ["path", "patches"],
            "additionalProperties": False,
        },
        output_type="json",
        output_schema=edit_output_schema(),
        metadata={
            "implementation_status": "native",
            "adapter_kind": "filesystem_write",
            "application_tags": ["filesystem", "codegen", "research"],
        },
    )


async def file_write_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Write text file in overwrite or append mode."""
    path = resolve_writable_path(
        args.get("path"), create_parent=bool(args.get("create_parent", False))
    )
    content = args.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    mode = str(args.get("mode") or "overwrite").strip().lower()
    if mode not in {"overwrite", "append"}:
        raise ValueError("mode must be one of: overwrite, append")
    max_bytes = as_int(args.get("max_bytes"), default=MAX_BYTES_DEFAULT, minimum=1)
    existed_before = path.exists()
    existing = (
        read_text_with_size_guard(path, max_bytes=max_bytes) if existed_before else ""
    )
    new_text = content if mode == "overwrite" else f"{existing}{content}"
    ensure_text_size(new_text, max_bytes=max_bytes)
    dry_run = bool(args.get("dry_run", False))
    preview_chars = as_int(
        args.get("preview_chars"), default=_PREVIEW_CHARS_DEFAULT, minimum=0
    )
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return {
        "summary": f"{mode} write {'previewed' if dry_run else 'completed'}: {path}",
        "path": str(path),
        "operation": "write",
        "mode": mode,
        "dry_run": dry_run,
        "created": not dry_run and not existed_before,
        "existed_before": existed_before,
        "bytes_written": len(content.encode("utf-8")),
        "replacements": 0,
        "size_bytes": len(new_text.encode("utf-8")),
        "preview": _preview(before=existing, after=new_text, max_chars=preview_chars),
    }


async def file_edit_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Replace expected old_text occurrences in file."""
    path = resolve_file_path(args.get("path"))
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not isinstance(old_text, str) or not old_text:
        raise ValueError("old_text must be a non-empty string")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be a string")
    expected = as_int(
        args.get("expected_occurrences"),
        default=1,
        minimum=1,
    )
    max_bytes = as_int(args.get("max_bytes"), default=MAX_BYTES_DEFAULT, minimum=1)
    source = read_text_with_size_guard(path, max_bytes=max_bytes)
    occurrences = source.count(old_text)
    if occurrences != expected:
        raise ValueError(
            f"old_text occurrences mismatch: expected {expected}, found {occurrences}"
        )
    updated = source.replace(old_text, new_text, expected)
    ensure_text_size(updated, max_bytes=max_bytes)
    dry_run = bool(args.get("dry_run", False))
    preview_chars = as_int(
        args.get("preview_chars"), default=_PREVIEW_CHARS_DEFAULT, minimum=0
    )
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    return {
        "summary": f"edit {'previewed' if dry_run else 'completed'}: {path}",
        "path": str(path),
        "operation": "edit",
        "dry_run": dry_run,
        "created": False,
        "existed_before": True,
        "replacements": expected,
        "size_bytes": len(updated.encode("utf-8")),
        "preview": _preview(before=source, after=updated, max_chars=preview_chars),
    }


async def file_patch_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Apply multiple exact replacements to a file."""
    path = resolve_file_path(args.get("path"))
    patches = _parse_patch_operations(args.get("patches"))
    max_bytes = as_int(args.get("max_bytes"), default=MAX_BYTES_DEFAULT, minimum=1)
    source = read_text_with_size_guard(path, max_bytes=max_bytes)
    updated = source
    applied: list[dict[str, Any]] = []
    replacement_total = 0
    for index, patch in enumerate(patches):
        old_text = patch["old_text"]
        new_text = patch["new_text"]
        expected = patch["expected_occurrences"]
        occurrences = updated.count(old_text)
        if occurrences != expected:
            raise ValueError(
                "patch old_text occurrences mismatch: "
                f"index={index}, expected {expected}, found {occurrences}"
            )
        updated = updated.replace(old_text, new_text, expected)
        replacement_total += expected
        applied.append(
            {
                "index": index,
                "replacements": expected,
                "old_bytes": len(old_text.encode("utf-8")),
                "new_bytes": len(new_text.encode("utf-8")),
            }
        )
    ensure_text_size(updated, max_bytes=max_bytes)
    dry_run = bool(args.get("dry_run", False))
    preview_chars = as_int(
        args.get("preview_chars"), default=_PREVIEW_CHARS_DEFAULT, minimum=0
    )
    if not dry_run:
        path.write_text(updated, encoding="utf-8")
    return {
        "summary": (
            f"patch {'previewed' if dry_run else 'completed'}: "
            f"{path} ({replacement_total} replacements)"
        ),
        "path": str(path),
        "operation": "patch",
        "dry_run": dry_run,
        "created": False,
        "existed_before": True,
        "replacements": replacement_total,
        "patches_applied": applied,
        "size_bytes": len(updated.encode("utf-8")),
        "preview": _preview(before=source, after=updated, max_chars=preview_chars),
    }


def _parse_patch_operations(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("patches must be a non-empty list")
    if len(raw) > 50:
        raise ValueError("patches must contain at most 50 items")
    parsed: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"patch item {index} must be an object")
        old_text = item.get("old_text")
        new_text = item.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ValueError(f"patch item {index} old_text must be non-empty")
        if not isinstance(new_text, str):
            raise ValueError(f"patch item {index} new_text must be a string")
        expected = as_int(
            item.get("expected_occurrences"),
            default=1,
            minimum=1,
        )
        parsed.append(
            {
                "old_text": old_text,
                "new_text": new_text,
                "expected_occurrences": expected,
            }
        )
    return parsed


def _preview(*, before: str, after: str, max_chars: int) -> dict[str, Any]:
    return {
        "before": before[:max_chars],
        "after": after[:max_chars],
        "truncated": len(before) > max_chars or len(after) > max_chars,
    }
