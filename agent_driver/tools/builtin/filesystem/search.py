"""Search builtins: glob_search and grep_search."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._paths import (
    as_int,
    depth_from_relative,
    is_ignored,
    load_ignore_patterns,
    resolve_base_dir,
)

GLOB_SEARCH_TOOL = "glob_search"
GREP_SEARCH_TOOL = "grep_search"
MAX_MATCHES_DEFAULT = 50
MAX_RESULTS_DEFAULT = 200
MAX_DEPTH_DEFAULT = 32
PREVIEW_LIMIT_DEFAULT = 2_000


@dataclass(frozen=True, slots=True)
class GrepConfig:
    """Normalized grep handler config."""

    base: Path
    path_glob: str | None
    max_matches: int
    max_results: int
    preview_chars: int
    ignored: list[str]


def glob_search_manifest() -> ToolManifest:
    """Build glob_search manifest."""
    return ToolManifest(
        name=GLOB_SEARCH_TOOL,
        description=(
            "Find workspace paths by glob pattern, respecting .gitignore. "
            "Pattern ending with '/' matches directories; other patterns match files."
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
                    "description": (
                        "Base directory; absolute or relative to workspace cwd; "
                        "defaults to cwd"
                    ),
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


def grep_search_manifest() -> ToolManifest:
    """Build grep_search manifest."""
    return ToolManifest(
        name=GREP_SEARCH_TOOL,
        description=("Search workspace files by regex content and return file/line matches."),
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
                    "description": (
                        "Search root; absolute or relative to workspace cwd; "
                        "defaults to cwd"
                    ),
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


async def glob_search_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Find files by glob under base directory."""
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    base = resolve_base_dir(args.get("base_dir"))
    max_results = as_int(args.get("max_results"), MAX_RESULTS_DEFAULT, minimum=1)
    max_depth = as_int(args.get("max_depth"), MAX_DEPTH_DEFAULT, minimum=0)
    ignored = load_ignore_patterns(base)
    rows: list[str] = []
    truncated = False
    directory_only = pattern.endswith("/")
    normalized_pattern = pattern if pattern.startswith("**/") else f"**/{pattern}"
    for path in sorted(base.rglob("*")):
        if len(rows) >= max_results:
            truncated = True
            break
        if directory_only and not path.is_dir():
            continue
        if not directory_only and path.is_dir():
            continue
        rel = path.relative_to(base).as_posix()
        if depth_from_relative(rel) > max_depth:
            continue
        if is_ignored(rel, ignored):
            continue
        if path.match(pattern) or path.match(normalized_pattern):
            rows.append(f"{rel}/" if directory_only else rel)
    payload = {
        "summary": f"{len(rows)} paths matched '{pattern}'",
        "base_dir": str(base),
        "pattern": pattern,
        "results": rows,
        "returned_count": len(rows),
        "truncated": truncated,
    }
    if truncated:
        payload["limit"] = "max_results"
        payload["limit_value"] = max_results
        payload["more_available"] = True
    return payload


async def grep_search_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Search files by regex content."""
    regex, pattern, config = parse_grep_args(args)
    matches, files_scanned, matched_files, skipped_files, limit_reason = scan_grep_matches(
        config=config,
        regex=regex,
    )
    payload: dict[str, Any] = {
        "summary": (
            f"{len(matches)} matches for /{pattern}/ in {matched_files} files "
            f"(scanned {files_scanned})"
        ),
        "base_dir": str(config.base),
        "pattern": pattern,
        "matches": matches,
        "returned_count": len(matches),
        "files_with_matches": matched_files,
        "files_scanned": files_scanned,
        "skipped_files_count": skipped_files,
        "truncated": limit_reason is not None,
    }
    if limit_reason is not None:
        payload["limit"] = limit_reason
        payload["limit_value"] = (
            config.max_matches if limit_reason == "max_matches" else config.max_results
        )
        payload["more_available"] = True
    return payload


def scan_grep_matches(
    *,
    config: GrepConfig,
    regex: re.Pattern[str],
) -> tuple[list[dict[str, Any]], int, int, int, str | None]:
    """Scan files and collect bounded grep matches."""
    matches: list[dict[str, Any]] = []
    files_scanned = 0
    matched_files = 0
    skipped_files = 0
    limit_reason: str | None = None
    for path in sorted(config.base.rglob("*")):
        if len(matches) >= config.max_matches:
            limit_reason = "max_matches"
            break
        if matched_files >= config.max_results:
            limit_reason = "max_results"
            break
        rel = searchable_relative_path(path=path, base=config.base, config=config)
        if rel is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped_files += 1
            continue
        files_scanned += 1
        file_rows, has_match = collect_line_matches(
            rel=rel,
            text=text,
            regex=regex,
            preview_chars=config.preview_chars,
            max_matches_left=config.max_matches - len(matches),
        )
        matches.extend(file_rows)
        if has_match:
            matched_files += 1
        if len(matches) >= config.max_matches and limit_reason is None:
            limit_reason = "max_matches"
    return matches, files_scanned, matched_files, skipped_files, limit_reason


def parse_grep_args(
    args: dict[str, Any],
) -> tuple[re.Pattern[str], str, GrepConfig]:
    """Parse grep args into regex and normalized config."""
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise ValueError("pattern is required")
    regex = re.compile(pattern)
    base = resolve_base_dir(args.get("base_dir"))
    raw_path_glob = str(args.get("path_glob") or "").strip()
    path_glob = raw_path_glob or None
    if path_glob and "/" not in path_glob:
        path_glob = f"**/{path_glob}"
    max_matches = as_int(args.get("max_matches"), MAX_MATCHES_DEFAULT, minimum=1)
    max_results = as_int(args.get("max_results"), MAX_RESULTS_DEFAULT, minimum=1)
    preview_chars = as_int(args.get("preview_chars"), PREVIEW_LIMIT_DEFAULT, minimum=16)
    ignored = load_ignore_patterns(base)
    config = GrepConfig(
        base,
        path_glob,
        max_matches,
        max_results,
        preview_chars,
        ignored,
    )
    return regex, pattern, config


def searchable_relative_path(
    *, path: Path, base: Path, config: GrepConfig
) -> str | None:
    """Return searchable relative path or None when filtered out."""
    if path.is_dir():
        return None
    rel = path.relative_to(base).as_posix()
    if is_ignored(rel, config.ignored):
        return None
    if config.path_glob and not fnmatch.fnmatch(rel, config.path_glob):
        return None
    return rel


def collect_line_matches(
    *,
    rel: str,
    text: str,
    regex: re.Pattern[str],
    preview_chars: int,
    max_matches_left: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Collect regex matches from one file."""
    rows: list[dict[str, Any]] = []
    has_match = False
    if max_matches_left <= 0:
        return rows, has_match
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not regex.search(line):
            continue
        has_match = True
        row: dict[str, Any] = {
            "path": rel,
            "line": line_number,
            "text": line[:preview_chars],
        }
        rows.append(row)
        if len(rows) >= max_matches_left:
            row["more_lines_in_file"] = True
            break
    return rows, has_match
