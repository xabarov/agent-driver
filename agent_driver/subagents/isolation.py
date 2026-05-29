"""Workspace isolation helpers for subagent child runs."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ChildWorkspace:
    """Prepared child workspace and optional cleanup target."""

    cwd: Path | None
    mode: str
    cleanup_path: Path | None = None
    git_root: Path | None = None


def prepare_child_workspace(
    *, parent_workspace_cwd: str | None, task_metadata: Mapping[str, Any]
) -> ChildWorkspace:
    """Prepare inherited, overridden, or git-worktree child workspace."""
    isolation_mode = str(task_metadata.get("isolation_mode") or "inherit").lower()
    if isolation_mode != "worktree":
        return ChildWorkspace(
            cwd=_resolve_child_workspace_cwd(
                parent_workspace_cwd=parent_workspace_cwd,
                task_metadata=task_metadata,
            ),
            mode=(
                "subagent_task"
                if task_metadata.get("cwd") or task_metadata.get("workspace_cwd")
                else "parent"
            ),
        )
    parent_root = _optional_workspace_path(parent_workspace_cwd)
    if parent_root is None:
        raise ValueError("worktree isolation requires parent workspace_cwd")
    git_root = _git_worktree_root(parent_root)
    temp_parent = Path(tempfile.mkdtemp(prefix="agent-driver-subagent-"))
    worktree_path = temp_parent / "worktree"
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "worktree",
                "add",
                "--detach",
                str(worktree_path),
                "HEAD",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        shutil.rmtree(temp_parent, ignore_errors=True)
        raise
    return ChildWorkspace(
        cwd=worktree_path.resolve(),
        mode="worktree",
        cleanup_path=temp_parent,
        git_root=git_root,
    )


def cleanup_child_workspace(workspace: ChildWorkspace) -> None:
    """Remove temporary workspace resources when a child reaches terminal state."""
    if workspace.cleanup_path is None:
        return
    worktree_path = workspace.cleanup_path / "worktree"
    if workspace.git_root is not None and worktree_path.exists():
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace.git_root),
                "worktree",
                "remove",
                "--force",
                str(worktree_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    shutil.rmtree(workspace.cleanup_path, ignore_errors=True)


def _resolve_child_workspace_cwd(
    *, parent_workspace_cwd: str | None, task_metadata: Mapping[str, Any]
) -> Path | None:
    parent_root = _optional_workspace_path(parent_workspace_cwd)
    raw_override = task_metadata.get("workspace_cwd") or task_metadata.get("cwd")
    if raw_override is None:
        return parent_root
    if parent_root is None:
        raise ValueError("subagent cwd override requires parent workspace_cwd")
    requested = Path(str(raw_override)).expanduser()
    if not requested.is_absolute():
        requested = parent_root / requested
    resolved = requested.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"subagent cwd is not an existing directory: {resolved}")
    try:
        resolved.relative_to(parent_root)
    except ValueError as exc:
        raise ValueError(
            f"subagent cwd outside parent workspace ({parent_root}): {resolved}"
        ) from exc
    return resolved


def _optional_workspace_path(raw: str | None) -> Path | None:
    if raw is None or not str(raw).strip():
        return None
    path = Path(str(raw)).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"parent workspace_cwd is not an existing directory: {path}")
    return path


def _git_worktree_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    root = Path(result.stdout.strip()).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"git worktree root is not an existing directory: {root}")
    return root


__all__ = ["ChildWorkspace", "cleanup_child_workspace", "prepare_child_workspace"]
