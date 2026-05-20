"""Run-scoped context for tool handlers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Iterator

_workspace_cwd: ContextVar[Path | None] = ContextVar("workspace_cwd", default=None)
_tool_call_context: ContextVar[dict[str, str] | None] = ContextVar(
    "tool_call_context", default=None
)


def get_workspace_cwd() -> Path:
    """Return run-scoped workspace cwd, fallback to process cwd."""
    current = _workspace_cwd.get()
    if current is None:
        return Path.cwd()
    return current


def get_workspace_jail_root() -> Path | None:
    """Return run-scoped workspace root when explicitly set, else None."""
    return _workspace_cwd.get()


def get_tool_call_context() -> dict[str, str]:
    """Return run-scoped tool call metadata."""
    payload = _tool_call_context.get()
    if not isinstance(payload, dict):
        return {}
    return dict(payload)


def set_workspace_cwd(path: Path | None) -> Token[Path | None]:
    """Set run-scoped workspace cwd and return reset token."""
    if path is None:
        return _workspace_cwd.set(None)
    return _workspace_cwd.set(path.resolve())


def set_tool_call_context(
    *, run_id: str | None = None, thread_id: str | None = None
) -> Token[dict[str, str] | None]:
    """Set run/thread metadata for tool handlers."""
    payload: dict[str, str] = {}
    if isinstance(run_id, str) and run_id.strip():
        payload["run_id"] = run_id.strip()
    if isinstance(thread_id, str) and thread_id.strip():
        payload["thread_id"] = thread_id.strip()
    if not payload:
        return _tool_call_context.set(None)
    return _tool_call_context.set(payload)


@contextmanager
def workspace_cwd_scope(path: Path | None) -> Iterator[None]:
    """Temporarily set workspace cwd for current task context."""
    token = set_workspace_cwd(path)
    try:
        yield
    finally:
        _workspace_cwd.reset(token)


@contextmanager
def tool_call_context_scope(
    *, run_id: str | None = None, thread_id: str | None = None
) -> Iterator[None]:
    """Temporarily set run/thread metadata for current tool handler call."""
    token = set_tool_call_context(run_id=run_id, thread_id=thread_id)
    try:
        yield
    finally:
        _tool_call_context.reset(token)


__all__ = [
    "get_workspace_cwd",
    "get_workspace_jail_root",
    "get_tool_call_context",
    "set_tool_call_context",
    "set_workspace_cwd",
    "tool_call_context_scope",
    "workspace_cwd_scope",
]
