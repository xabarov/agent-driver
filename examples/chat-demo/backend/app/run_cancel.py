"""Cooperative run cancellation for chat demo streaming."""

from __future__ import annotations

import contextvars

_active_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "active_run_id",
    default=None,
)
_cancelled_runs: set[str] = set()


def set_active_run(run_id: str | None) -> contextvars.Token[str | None]:
    """Bind the current asyncio task to one run id for cancellation probes."""
    return _active_run_id.set(run_id)


def reset_active_run(token: contextvars.Token[str | None]) -> None:
    """Restore previous active run binding."""
    _active_run_id.reset(token)


def request_cancel(run_id: str) -> None:
    """Mark a run as cancelled; runner probes this between steps."""
    _cancelled_runs.add(run_id)


def clear_cancel(run_id: str) -> None:
    """Remove cancellation mark after run ends."""
    _cancelled_runs.discard(run_id)


def is_cancelled(run_id: str) -> bool:
    """Return whether run_id was marked cancelled."""
    return run_id in _cancelled_runs


def cancellation_probe() -> bool:
    """RunnerConfig probe: true when active run was cancelled."""
    run_id = _active_run_id.get()
    return run_id is not None and run_id in _cancelled_runs


def reset_caches_for_tests() -> None:
    """Clear cancellation state (tests only)."""
    _cancelled_runs.clear()
    _active_run_id.set(None)
