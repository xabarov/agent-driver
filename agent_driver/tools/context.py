"""Run-scoped context for tool handlers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_driver.tools.cancellation import ToolCancellation

_workspace_cwd: ContextVar[Path | None] = ContextVar("workspace_cwd", default=None)
_tool_call_context: ContextVar[dict[str, str] | None] = ContextVar(
    "tool_call_context", default=None
)


@runtime_checkable
class AsyncFileIO(Protocol):
    """Run-scoped async source/sink for the filesystem tools' bytes.

    When set (via :func:`fs_io_scope`), the builtin read/write/edit tools route
    their file reads and writes through this object instead of local disk —
    e.g. an ACP adapter routes them to the editor so edits land in the user's
    buffers. Path validation / jailing still happens against the local
    workspace; only the byte transfer is redirected.
    """

    async def read_text(self, path: str) -> str:
        """Return the text content of ``path``."""

    async def write_text(self, path: str, content: str) -> None:
        """Write ``content`` to ``path``."""


_fs_io: ContextVar["AsyncFileIO | None"] = ContextVar("fs_io", default=None)


@runtime_checkable
class AsyncCommandRunner(Protocol):
    """Run-scoped executor for the shell tool's commands.

    When set (via :func:`command_runner_scope`), the builtin ``bash`` tool runs
    its (already policy-checked) command through this object instead of a local
    subprocess — e.g. an ACP adapter runs it in the editor's terminal. Returns
    the same shape the local executor does:
    ``{"stdout", "stderr", "timed_out", "exit_code"}``.
    """

    async def run_command(
        self, command: str, *, cwd: str, timeout_seconds: float
    ) -> dict[str, Any]:
        """Execute ``command`` and return its stdout/stderr/exit result."""


_command_runner: ContextVar["AsyncCommandRunner | None"] = ContextVar(
    "command_runner", default=None
)


# Phase 11 H16 — optional progress reporter for long-running tools.
# Stored as a ContextVar so handlers don't need an extra parameter; the
# context is set up by the executor immediately before invoking the
# handler and cleared on return. ``None`` means progress reporting is a
# no-op for this call (default for hosts that don't wire it).
@dataclass(frozen=True, slots=True)
class ToolProgress:
    """One progress update emitted by a running tool handler.

    Fields:
        kind: free-form short label (e.g. ``"scan"``, ``"download"``,
            ``"validate"``). Used by stream consumers for routing and
            for display ("Scanning 100/300 hosts").
        message: human-friendly status line, suitable for display in a
            CLI or chat-UI timeline.
        completion_ratio: optional float in ``[0.0, 1.0]`` reporting
            estimated completion. ``None`` when unknown / indeterminate.
        data: optional structured payload for richer consumers. Must be
            JSON-serializable (the runtime projector will serialize via
            ``model_dump`` / ``json.dumps``); keep small.
    """

    kind: str
    message: str
    completion_ratio: float | None = None
    data: dict[str, Any] = field(default_factory=dict)


ProgressReporter = Callable[[ToolProgress], None]


_on_progress: ContextVar[ProgressReporter | None] = ContextVar(
    "tool_on_progress", default=None
)

_tool_cancellation: ContextVar["ToolCancellation | None"] = ContextVar(
    "tool_cancellation", default=None
)

_tool_attempt_epoch: ContextVar[int] = ContextVar("tool_attempt_epoch", default=0)


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


def get_fs_io() -> "AsyncFileIO | None":
    """Return the run-scoped async file IO, or ``None`` for local disk."""
    return _fs_io.get()


def get_command_runner() -> "AsyncCommandRunner | None":
    """Return the run-scoped command runner, or ``None`` for local subprocess."""
    return _command_runner.get()


def report_tool_progress(
    *,
    kind: str,
    message: str,
    completion_ratio: float | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Emit a progress update for the currently running tool handler.

    Phase 11 H16 — handlers may call this freely; when no reporter is
    wired (default), the call is a silent no-op. When the host wires
    a reporter via ``tool_progress_scope()``, each invocation produces
    a ``RuntimeEventType.TOOL_PROGRESS`` event correlated with the
    current ``tool_call_id``.

    Errors raised by the reporter are swallowed (logged at WARNING)
    so a misbehaving observability sink can never crash a tool
    handler.
    """
    reporter = _on_progress.get()
    if reporter is None:
        return
    progress = ToolProgress(
        kind=kind,
        message=message,
        completion_ratio=completion_ratio,
        data=dict(data) if data else {},
    )
    try:
        reporter(progress)
    except Exception:  # pragma: no cover - defensive isolation
        import logging

        logging.getLogger(__name__).warning(
            "tool_progress reporter raised; swallowing", exc_info=True
        )


def current_tool_cancellation() -> "ToolCancellation | None":
    """Return the cooperative cancellation signal for the running tool call.

    U4 — handlers may call this freely; when the runtime did not plumb an abort
    handle into the executor (default), it returns ``None`` and the handler runs
    unbounded as before. When present, ``token.is_cancelled`` / ``await
    token.wait_cancelled()`` let a cooperative handler stop its own external
    work (socket, browser, long query) when the run is aborted.
    """
    return _tool_cancellation.get()


@contextmanager
def tool_cancellation_scope(
    token: "ToolCancellation | None",
) -> Iterator[None]:
    """Temporarily expose ``token`` to the running tool handler call."""
    reset = _tool_cancellation.set(token)
    try:
        yield
    finally:
        _tool_cancellation.reset(reset)


def current_tool_attempt_epoch() -> int:
    """Return the execution-attempt epoch of the running run (F1 / U4).

    0 for a fresh run; bumped on each resume that re-drives the run. A handler
    may consult it to tag externally-visible side effects with the attempt they
    belong to, so a straggler from a superseded attempt is recognisable.
    """
    return _tool_attempt_epoch.get()


@contextmanager
def tool_attempt_epoch_scope(epoch: int) -> Iterator[None]:
    """Temporarily expose the run's execution-attempt epoch to handlers."""
    reset = _tool_attempt_epoch.set(int(epoch))
    try:
        yield
    finally:
        _tool_attempt_epoch.reset(reset)


def set_workspace_cwd(path: Path | None) -> Token[Path | None]:
    """Set run-scoped workspace cwd and return reset token."""
    if path is None:
        return _workspace_cwd.set(None)
    return _workspace_cwd.set(path.resolve())


def set_tool_call_context(
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    tool_call_id: str | None = None,
    attempt_id: str | None = None,
) -> Token[dict[str, str] | None]:
    """Set executor-owned run/thread/call metadata for tool handlers."""
    payload: dict[str, str] = {}
    for key, value in (
        ("run_id", run_id),
        ("thread_id", thread_id),
        ("tool_call_id", tool_call_id),
        ("attempt_id", attempt_id),
    ):
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()
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
    *,
    run_id: str | None = None,
    thread_id: str | None = None,
    tool_call_id: str | None = None,
    attempt_id: str | None = None,
) -> Iterator[None]:
    """Temporarily set executor-owned identity for the current tool handler."""
    token = set_tool_call_context(
        run_id=run_id,
        thread_id=thread_id,
        tool_call_id=tool_call_id,
        attempt_id=attempt_id,
    )
    try:
        yield
    finally:
        _tool_call_context.reset(token)


@contextmanager
def fs_io_scope(file_io: "AsyncFileIO | None") -> Iterator[None]:
    """Temporarily route filesystem-tool reads/writes through ``file_io``.

    Set by a host (e.g. the ACP adapter) around a run; the builtin filesystem
    tools pick it up via :func:`get_fs_io`. ``None`` keeps the default
    local-disk behavior.
    """
    token = _fs_io.set(file_io)
    try:
        yield
    finally:
        _fs_io.reset(token)


@contextmanager
def command_runner_scope(runner: "AsyncCommandRunner | None") -> Iterator[None]:
    """Temporarily route the shell tool's execution through ``runner``.

    Set by a host (e.g. the ACP adapter) around a run; the builtin ``bash`` tool
    picks it up via :func:`get_command_runner`. ``None`` keeps the default local
    subprocess behavior.
    """
    token = _command_runner.set(runner)
    try:
        yield
    finally:
        _command_runner.reset(token)


@contextmanager
def tool_progress_scope(reporter: ProgressReporter | None) -> Iterator[None]:
    """Phase 11 H16 — wire a progress reporter for the current tool call.

    Hosts set up a reporter (e.g. a closure that captures ``call_id`` +
    runtime emit) immediately before invoking a tool handler. When the
    handler calls :func:`report_tool_progress`, the reporter is invoked
    synchronously; on scope exit the reporter is cleared so it never
    leaks across tool calls.

    Pass ``None`` to install a no-op (e.g. when the host doesn't care
    about progress for a particular call).
    """
    token = _on_progress.set(reporter)
    try:
        yield
    finally:
        _on_progress.reset(token)


__all__ = [
    "AsyncCommandRunner",
    "AsyncFileIO",
    "ProgressReporter",
    "ToolProgress",
    "command_runner_scope",
    "fs_io_scope",
    "get_command_runner",
    "get_fs_io",
    "get_workspace_cwd",
    "get_workspace_jail_root",
    "get_tool_call_context",
    "report_tool_progress",
    "set_tool_call_context",
    "set_workspace_cwd",
    "tool_call_context_scope",
    "tool_progress_scope",
    "workspace_cwd_scope",
]
