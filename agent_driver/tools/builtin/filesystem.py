"""Filesystem and codebase analysis built-in tools."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolRegistry

_READ_FILE_TOOL = "read_file"
_GLOB_SEARCH_TOOL = "glob_search"
_GREP_SEARCH_TOOL = "grep_search"
_FILE_WRITE_TOOL = "file_write"
_FILE_EDIT_TOOL = "file_edit"
_NOTEBOOK_EDIT_TOOL = "notebook_edit"
_MAX_BYTES_DEFAULT = 64_000
_MAX_MATCHES_DEFAULT = 50
_MAX_RESULTS_DEFAULT = 200
_MAX_DEPTH_DEFAULT = 32
_MAX_OFFSET_DEFAULT = 1_000_000
_PREVIEW_LIMIT_DEFAULT = 2_000
_NOTEBOOK_SUPPORTED_CELL_TYPES = {"code", "markdown", "raw"}


@dataclass(frozen=True, slots=True)
class _GrepConfig:
    base: Path
    path_glob: str | None
    max_matches: int
    max_results: int
    preview_chars: int
    ignored: list[str]


@dataclass(frozen=True, slots=True)
class _NotebookEditRequest:
    path: Path
    cell_idx: int
    is_new_cell: bool
    old_text: str
    new_text: str
    cell_type: str
    max_bytes: int


def register_filesystem_tools(registry: ToolRegistry) -> None:
    """Register built-in read/search tools for local codebase analysis."""
    registry.register(_read_file_manifest(), _read_file_handler)
    registry.register(_glob_search_manifest(), _glob_search_handler)
    registry.register(_grep_search_manifest(), _grep_search_handler)
    registry.register(_file_write_manifest(), _file_write_handler)
    registry.register(_file_edit_manifest(), _file_edit_handler)
    registry.register(_notebook_edit_manifest(), _notebook_edit_handler)


def _read_file_manifest() -> ToolManifest:
    return ToolManifest(
        name=_READ_FILE_TOOL,
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
                "path": {"type": "string", "description": "Absolute file path"},
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


def _glob_search_manifest() -> ToolManifest:
    return ToolManifest(
        name=_GLOB_SEARCH_TOOL,
        description=(
            "Find file paths in workspace by glob pattern, respecting .gitignore."
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
                "pattern": {"type": "string", "description": "Glob pattern"},
                "base_dir": {
                    "type": "string",
                    "description": "Absolute base directory; defaults to cwd",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Maximum result paths to return",
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 64,
                    "description": "Maximum traversal depth from base_dir",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _grep_search_manifest() -> ToolManifest:
    return ToolManifest(
        name=_GREP_SEARCH_TOOL,
        description=(
            "Search workspace files by regex content and return file/line matches."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=15.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regex pattern"},
                "base_dir": {
                    "type": "string",
                    "description": "Absolute search root; defaults to cwd",
                },
                "path_glob": {
                    "type": "string",
                    "description": "Optional fnmatch-style path filter",
                },
                "max_matches": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum content matches to return",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Maximum files scanned with matches",
                },
                "preview_chars": {
                    "type": "integer",
                    "minimum": 16,
                    "maximum": 8000,
                    "description": "Maximum chars per returned line preview",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _file_write_manifest() -> ToolManifest:
    return ToolManifest(
        name=_FILE_WRITE_TOOL,
        description=(
            "Write UTF-8 text to an absolute file path with overwrite/append mode."
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
                "path": {"type": "string", "description": "Absolute file path"},
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
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _file_edit_manifest() -> ToolManifest:
    return ToolManifest(
        name=_FILE_EDIT_TOOL,
        description=(
            "Edit UTF-8 text file by replacing expected old_text occurrences with "
            "new_text."
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
                "path": {"type": "string", "description": "Absolute file path"},
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
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _notebook_edit_manifest() -> ToolManifest:
    return ToolManifest(
        name=_NOTEBOOK_EDIT_TOOL,
        description=(
            "Edit a Jupyter notebook cell by replacing old_text or adding a new cell."
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
                "path": {
                    "type": "string",
                    "description": "Absolute path to .ipynb file",
                },
                "cell_idx": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100_000,
                    "description": "0-based notebook cell index",
                },
                "is_new_cell": {
                    "type": "boolean",
                    "description": "When true, insert new cell at cell_idx",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown", "raw"],
                    "description": "Cell type for new cells",
                },
                "old_text": {
                    "type": "string",
                    "description": "Expected source snippet in existing cell",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement or new cell source text",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2_000_000,
                    "description": "Maximum allowed notebook size",
                },
            },
            "required": ["path", "cell_idx", "is_new_cell", "old_text", "new_text"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _read_file_handler(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_file_path(args.get("path"))
    max_bytes = _as_int(args.get("max_bytes"), default=_MAX_BYTES_DEFAULT, minimum=1)
    raw = _read_text_with_size_guard(path, max_bytes=max_bytes)
    lines = raw.splitlines()
    sliced, start_index = _slice_lines(
        lines,
        offset=_as_optional_int(args.get("offset")),
        limit=_as_optional_int(args.get("limit")),
    )
    numbered = [
        f"{idx + 1}|{value}" for idx, value in enumerate(sliced, start=start_index)
    ]
    summary = f"{path} ({len(sliced)} lines)"
    return {
        "summary": summary,
        "path": str(path),
        "line_count": len(lines),
        "returned_lines": len(sliced),
        "content": "\n".join(numbered),
    }


async def _glob_search_handler(args: dict[str, Any]) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    base = _resolve_base_dir(args.get("base_dir"))
    max_results = _as_int(args.get("max_results"), _MAX_RESULTS_DEFAULT, minimum=1)
    max_depth = _as_int(args.get("max_depth"), _MAX_DEPTH_DEFAULT, minimum=0)
    ignored = _load_ignore_patterns(base)
    rows: list[str] = []
    normalized_pattern = pattern if pattern.startswith("**/") else f"**/{pattern}"
    for path in sorted(base.rglob("*")):
        if len(rows) >= max_results:
            break
        if path.is_dir():
            continue
        rel = path.relative_to(base).as_posix()
        if _depth_from_relative(rel) > max_depth:
            continue
        if _is_ignored(rel, ignored):
            continue
        if path.match(pattern) or path.match(normalized_pattern):
            rows.append(rel)
    return {
        "summary": f"{len(rows)} paths matched '{pattern}'",
        "base_dir": str(base),
        "pattern": pattern,
        "results": rows,
    }


async def _grep_search_handler(args: dict[str, Any]) -> dict[str, Any]:
    regex, pattern, config = _parse_grep_args(args)
    matches, files_scanned, matched_files = _scan_grep_matches(
        config=config,
        regex=regex,
    )
    return {
        "summary": (
            f"{len(matches)} matches for /{pattern}/ in {matched_files} files "
            f"(scanned {files_scanned})"
        ),
        "base_dir": str(config.base),
        "pattern": pattern,
        "matches": matches,
    }


async def _file_write_handler(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_writable_path(
        args.get("path"), create_parent=bool(args.get("create_parent", False))
    )
    content = args.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    mode = str(args.get("mode") or "overwrite").strip().lower()
    if mode not in {"overwrite", "append"}:
        raise ValueError("mode must be one of: overwrite, append")
    max_bytes = _as_int(args.get("max_bytes"), default=_MAX_BYTES_DEFAULT, minimum=1)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    new_text = content if mode == "overwrite" else f"{existing}{content}"
    _ensure_text_size(new_text, max_bytes=max_bytes)
    path.write_text(new_text, encoding="utf-8")
    return {
        "summary": f"{mode} write completed: {path}",
        "path": str(path),
        "mode": mode,
        "bytes_written": len(content.encode("utf-8")),
        "size_bytes": len(new_text.encode("utf-8")),
    }


async def _file_edit_handler(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_file_path(args.get("path"))
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not isinstance(old_text, str) or not old_text:
        raise ValueError("old_text must be a non-empty string")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be a string")
    expected = _as_int(
        args.get("expected_occurrences"),
        default=1,
        minimum=1,
    )
    max_bytes = _as_int(args.get("max_bytes"), default=_MAX_BYTES_DEFAULT, minimum=1)
    source = _read_text_with_size_guard(path, max_bytes=max_bytes)
    occurrences = source.count(old_text)
    if occurrences != expected:
        raise ValueError(
            f"old_text occurrences mismatch: expected {expected}, found {occurrences}"
        )
    updated = source.replace(old_text, new_text, expected)
    _ensure_text_size(updated, max_bytes=max_bytes)
    path.write_text(updated, encoding="utf-8")
    return {
        "summary": f"edit completed: {path}",
        "path": str(path),
        "replacements": expected,
        "size_bytes": len(updated.encode("utf-8")),
    }


async def _notebook_edit_handler(args: dict[str, Any]) -> dict[str, Any]:
    request = _parse_notebook_edit_request(args)
    notebook_text = _read_text_with_size_guard(request.path, max_bytes=request.max_bytes)
    notebook = _parse_notebook_payload(notebook_text)
    cells = notebook["cells"]
    if request.is_new_cell:
        if request.cell_idx > len(cells):
            raise ValueError(f"cell_idx out of range for insert: {request.cell_idx}")
        cells.insert(
            request.cell_idx,
            _new_notebook_cell(cell_type=request.cell_type, text=request.new_text),
        )
        operation = "insert"
    else:
        if request.cell_idx >= len(cells):
            raise ValueError(f"cell_idx out of range: {request.cell_idx}")
        if not request.old_text:
            raise ValueError("old_text must be non-empty for existing cell edits")
        cell = cells[request.cell_idx]
        source = _notebook_cell_source_as_text(cell)
        count = source.count(request.old_text)
        if count != 1:
            raise ValueError(f"old_text must appear exactly once, found {count}")
        updated = source.replace(request.old_text, request.new_text, 1)
        cell["source"] = _notebook_source_lines(updated)
        operation = "replace"
    rendered = json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"
    _ensure_text_size(rendered, max_bytes=request.max_bytes)
    request.path.write_text(rendered, encoding="utf-8")
    return {
        "summary": f"notebook_edit {operation} completed: {request.path}",
        "path": str(request.path),
        "cell_idx": request.cell_idx,
        "operation": operation,
        "cell_count": len(cells),
        "size_bytes": len(rendered.encode("utf-8")),
    }


def _scan_grep_matches(
    *,
    config: _GrepConfig,
    regex: re.Pattern[str],
) -> tuple[list[dict[str, Any]], int, int]:
    matches: list[dict[str, Any]] = []
    files_scanned = 0
    matched_files = 0
    for path in sorted(config.base.rglob("*")):
        if len(matches) >= config.max_matches or matched_files >= config.max_results:
            break
        rel = _searchable_relative_path(path=path, base=config.base, config=config)
        if rel is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files_scanned += 1
        file_rows, has_match = _collect_line_matches(
            rel=rel,
            text=text,
            regex=regex,
            preview_chars=config.preview_chars,
            max_matches_left=config.max_matches - len(matches),
        )
        matches.extend(file_rows)
        if has_match:
            matched_files += 1
    return matches, files_scanned, matched_files


def _parse_grep_args(
    args: dict[str, Any],
) -> tuple[re.Pattern[str], str, _GrepConfig]:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise ValueError("pattern is required")
    regex = re.compile(pattern)
    base = _resolve_base_dir(args.get("base_dir"))
    path_glob = str(args.get("path_glob") or "").strip() or None
    max_matches = _as_int(args.get("max_matches"), _MAX_MATCHES_DEFAULT, minimum=1)
    max_results = _as_int(args.get("max_results"), _MAX_RESULTS_DEFAULT, minimum=1)
    preview_chars = _as_int(
        args.get("preview_chars"), _PREVIEW_LIMIT_DEFAULT, minimum=16
    )
    ignored = _load_ignore_patterns(base)
    config = _GrepConfig(
        base,
        path_glob,
        max_matches,
        max_results,
        preview_chars,
        ignored,
    )
    return regex, pattern, config


def _searchable_relative_path(
    *, path: Path, base: Path, config: _GrepConfig
) -> str | None:
    if path.is_dir():
        return None
    rel = path.relative_to(base).as_posix()
    if _is_ignored(rel, config.ignored):
        return None
    if config.path_glob and not fnmatch.fnmatch(rel, config.path_glob):
        return None
    return rel


def _collect_line_matches(
    *,
    rel: str,
    text: str,
    regex: re.Pattern[str],
    preview_chars: int,
    max_matches_left: int,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    has_match = False
    if max_matches_left <= 0:
        return rows, has_match
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not regex.search(line):
            continue
        has_match = True
        rows.append(
            {
                "path": rel,
                "line": line_number,
                "text": line[:preview_chars],
            }
        )
        if len(rows) >= max_matches_left:
            break
    return rows, has_match


def _parse_notebook_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid .ipynb JSON payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("notebook payload must be an object")
    cells = payload.get("cells")
    if not isinstance(cells, list):
        raise ValueError("notebook must contain a cells list")
    return payload


def _parse_notebook_edit_request(args: dict[str, Any]) -> _NotebookEditRequest:
    path = _resolve_file_path(args.get("path"))
    if path.suffix.lower() != ".ipynb":
        raise ValueError("path must point to a .ipynb file")
    cell_idx = _as_int(args.get("cell_idx"), default=0, minimum=0)
    is_new_cell = bool(args.get("is_new_cell"))
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if not isinstance(old_text, str):
        raise ValueError("old_text must be a string")
    if not isinstance(new_text, str):
        raise ValueError("new_text must be a string")
    cell_type = str(args.get("cell_type") or "markdown").strip().lower()
    if cell_type not in _NOTEBOOK_SUPPORTED_CELL_TYPES:
        raise ValueError("cell_type must be one of: code, markdown, raw")
    max_bytes = _as_int(args.get("max_bytes"), default=512_000, minimum=1)
    return _NotebookEditRequest(
        path=path,
        cell_idx=cell_idx,
        is_new_cell=is_new_cell,
        old_text=old_text,
        new_text=new_text,
        cell_type=cell_type,
        max_bytes=max_bytes,
    )


def _new_notebook_cell(*, cell_type: str, text: str) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": _notebook_source_lines(text),
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _notebook_cell_source_as_text(cell: dict[str, Any]) -> str:
    raw = cell.get("source")
    if isinstance(raw, list):
        parts = [item for item in raw if isinstance(item, str)]
        return "".join(parts)
    if isinstance(raw, str):
        return raw
    return ""


def _notebook_source_lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines(keepends=True)


def _resolve_file_path(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    if not path.exists():
        raise ValueError(f"path does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"path is not a file: {path}")
    return path


def _resolve_base_dir(raw: Any) -> Path:
    if raw is None:
        base = Path.cwd()
    elif isinstance(raw, str) and raw.strip():
        base = Path(raw).expanduser()
    else:
        raise ValueError("base_dir must be a non-empty string when provided")
    if not base.is_absolute():
        raise ValueError("base_dir must be absolute")
    if not base.exists():
        raise ValueError(f"base_dir does not exist: {base}")
    if not base.is_dir():
        raise ValueError(f"base_dir is not a directory: {base}")
    return base


def _resolve_writable_path(raw: Any, *, create_parent: bool) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    if path.exists() and path.is_dir():
        raise ValueError(f"path is not a file: {path}")
    parent = path.parent
    if not parent.exists():
        if not create_parent:
            raise ValueError(
                f"parent directory does not exist: {parent}; set create_parent=true"
            )
        parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise ValueError(f"parent path is not a directory: {parent}")
    return path


def _read_text_with_size_guard(path: Path, *, max_bytes: int) -> str:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file exceeds max_bytes ({size}>{max_bytes})")
    return path.read_text(encoding="utf-8")


def _ensure_text_size(text: str, *, max_bytes: int) -> None:
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"content exceeds max_bytes ({size}>{max_bytes})")


def _slice_lines(
    lines: list[str],
    *,
    offset: int | None,
    limit: int | None,
) -> tuple[list[str], int]:
    if not lines:
        return [], 0
    line_count = len(lines)
    if offset is None:
        start_index = 0
    elif offset > 0:
        start_index = min(offset - 1, line_count)
    else:
        start_index = max(line_count + offset, 0)
    if limit is None:
        end_index = line_count
    else:
        end_index = min(start_index + max(limit, 0), line_count)
    return lines[start_index:end_index], start_index


def _as_int(raw: Any, default: int, *, minimum: int) -> int:
    if raw is None:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


def _as_optional_int(raw: Any) -> int | None:
    if raw is None:
        return None
    value = int(raw)
    if abs(value) > _MAX_OFFSET_DEFAULT:
        raise ValueError("offset/limit value too large")
    return value


def _load_ignore_patterns(base: Path) -> list[str]:
    ignore_file = base / ".gitignore"
    if not ignore_file.exists():
        return []
    lines = ignore_file.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def _is_ignored(relative_path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def _depth_from_relative(relative_path: str) -> int:
    if not relative_path:
        return 0
    return relative_path.count("/")


__all__ = ["register_filesystem_tools"]
