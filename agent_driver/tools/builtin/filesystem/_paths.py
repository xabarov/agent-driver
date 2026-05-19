"""Shared path and payload helpers for filesystem tools."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

MAX_BYTES_DEFAULT = 64_000
MAX_OFFSET_DEFAULT = 1_000_000


def resolve_file_path(raw: Any) -> Path:
    """Resolve an absolute existing file path."""
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


def resolve_base_dir(raw: Any) -> Path:
    """Resolve an absolute existing directory path."""
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


def resolve_writable_path(raw: Any, *, create_parent: bool) -> Path:
    """Resolve target file path, optionally creating parent directories."""
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
    if not patterns:
        return False
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def depth_from_relative(relative_path: str) -> int:
    """Compute slash depth for a relative path."""
    if not relative_path:
        return 0
    return relative_path.count("/")
