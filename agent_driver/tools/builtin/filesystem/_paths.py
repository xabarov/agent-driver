"""Shared path and payload helpers for filesystem tools."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from agent_driver.tools.context import get_workspace_cwd

MAX_BYTES_DEFAULT = 64_000
MAX_OFFSET_DEFAULT = 1_000_000
_ALWAYS_IGNORED_PREFIXES = (
    ".git/",
    ".venv/",
    "__pycache__/",
    "node_modules/",
)


def resolve_file_path(raw: Any) -> Path:
    """Resolve an absolute existing file path."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (get_workspace_cwd() / path).resolve()
    if not path.exists():
        raise ValueError(f"path does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"path is not a file: {path}")
    return path


def resolve_base_dir(raw: Any) -> Path:
    """Resolve an absolute existing directory path."""
    if raw is None:
        base = get_workspace_cwd()
    elif isinstance(raw, str) and raw.strip():
        base = Path(raw).expanduser()
        if not base.is_absolute():
            base = (get_workspace_cwd() / base).resolve()
    else:
        raise ValueError("base_dir must be a non-empty string when provided")
    if not base.is_absolute():
        raise ValueError("base_dir must be absolute")
    if not base.exists():
        raise ValueError(f"base_dir does not exist: {base}")
    if not base.is_dir():
        raise ValueError(f"base_dir is not a directory: {base}")
    return base


def resolve_writable_path(raw: Any, *, create_parent: bool) -> Path:
    """Resolve target file path, optionally creating parent directories."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path must be a non-empty string")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (get_workspace_cwd() / path).resolve()
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


def read_text_with_size_guard(path: Path, *, max_bytes: int) -> str:
    """Read UTF-8 file after enforcing max byte size."""
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file exceeds max_bytes ({size}>{max_bytes})")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def ensure_text_size(text: str, *, max_bytes: int) -> None:
    """Validate encoded UTF-8 payload size."""
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"content exceeds max_bytes ({size}>{max_bytes})")


def as_int(raw: Any, default: int, *, minimum: int) -> int:
    """Parse optional integer with lower bound."""
    if raw is None:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


def as_optional_int(raw: Any) -> int | None:
    """Parse optional integer, bounded for safety."""
    if raw is None:
        return None
    value = int(raw)
    if abs(value) > MAX_OFFSET_DEFAULT:
        raise ValueError("offset/limit value too large")
    return value


def load_ignore_patterns(base: Path) -> list[str]:
    """Load simple ignore patterns from local .gitignore."""
    ignore_file = base / ".gitignore"
    if not ignore_file.exists():
        return []
    lines = ignore_file.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def is_ignored(relative_path: str, patterns: list[str]) -> bool:
    """Check if relative path matches one of ignore patterns."""
    rel = relative_path.strip("/")
    if not rel:
        return False
    rel_with_slash = f"{rel}/"
    for prefix in _ALWAYS_IGNORED_PREFIXES:
        if rel_with_slash.startswith(prefix):
            return True
    ignored = False
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:].strip()
            if not pattern:
                continue
        if _pattern_matches(rel, pattern):
            ignored = not negate
    return ignored


def _pattern_matches(relative_path: str, pattern: str) -> bool:
    normalized = pattern.lstrip("/")
    if not normalized:
        return False
    rel = relative_path.strip("/")
    rel_with_slash = f"{rel}/"
    if normalized.endswith("/"):
        prefix = normalized
        return rel_with_slash.startswith(prefix) or f"/{prefix}" in f"/{rel_with_slash}"
    if "/" not in normalized:
        return fnmatch.fnmatch(Path(rel).name, normalized)
    return fnmatch.fnmatch(rel, normalized)


def depth_from_relative(relative_path: str) -> int:
    """Compute slash depth for a relative path."""
    if not relative_path:
        return 0
    return relative_path.count("/")
