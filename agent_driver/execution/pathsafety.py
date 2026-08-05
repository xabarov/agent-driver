"""Backend-relative workspace path validation (EPIC-03 WP-C).

A local ``Path.resolve()`` cannot validate remote state and would leak the local
filesystem, so workspace paths are validated LEXICALLY against the lease's
``WorkspacePaths`` contract: ``..`` traversal that escapes the workspace root is
rejected without touching any disk, and writes are confined to the declared
writable roots. Symlink escape is a backend concern — unless the backend
explicitly allows it (``WorkspacePaths.allow_symlink_escape``), the routing layer
treats a path that lexically escapes the root as a rejection.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from agent_driver.contracts.execution_lease import WorkspacePaths
from agent_driver.execution.errors import ExecutionError


class WorkspacePathError(ExecutionError):
    """A workspace path escaped its root or a non-writable location — a bounded,
    governed rejection (never a raw local path leak)."""

    code = "workspace_path_denied"


def _normalize(path: str, *, root: PurePosixPath) -> PurePosixPath:
    """Lexically resolve ``path`` against ``root``, collapsing ``.``/``..``
    WITHOUT touching disk. A ``..`` that would rise above ``root`` raises."""
    candidate = PurePosixPath(path)
    base = root if not candidate.is_absolute() else PurePosixPath("/")
    parts: list[str] = list(base.parts)
    for segment in candidate.parts:
        if segment in ("", "."):
            continue
        if segment == "..":
            # Never allow rising above the filesystem anchor; the root-escape
            # check below is what actually enforces the workspace boundary.
            if len(parts) > 1:
                parts.pop()
            continue
        parts.append(segment)
    return PurePosixPath(*parts) if parts else PurePosixPath("/")


def _is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_workspace_path(
    path: str,
    paths: WorkspacePaths,
    *,
    require_writable: bool = False,
) -> str:
    """Return the normalized backend-relative path, or raise
    :class:`WorkspacePathError`.

    - A relative path is resolved under ``workspace_root``.
    - Any path (absolute or relative) that lexically escapes ``workspace_root``
      is rejected (unless ``allow_symlink_escape`` — reserved, still rejected on
      lexical escape here).
    - When ``require_writable`` and ``writable_roots`` is non-empty, the path
      must fall under one of them.
    """
    if not isinstance(path, str) or not path.strip():
        raise WorkspacePathError("path must be a non-empty string")
    root = PurePosixPath(paths.workspace_root)
    normalized = _normalize(path, root=root)
    if not _is_within(normalized, root):
        raise WorkspacePathError("path escapes the workspace root")
    if require_writable and paths.writable_roots:
        if not any(
            _is_within(normalized, PurePosixPath(w)) for w in paths.writable_roots
        ):
            raise WorkspacePathError("path is not under a writable root")
    return str(normalized)


__all__ = ["validate_workspace_path", "WorkspacePathError"]
