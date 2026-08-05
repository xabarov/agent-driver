"""Adapt an ``ExecutionBackend`` down to the legacy run-scoped seams (EPIC-01).

The built-in ``bash``/``read``/``write`` tools already route through the
run-scoped :class:`~agent_driver.tools.context.AsyncCommandRunner` /
:class:`~agent_driver.tools.context.AsyncFileIO`. Rather than rewrite those
tools, EPIC-01 installs these thin adapters into the existing scopes: the tools
stay byte-for-byte unchanged, while their command and file bytes flow through the
injected backend.

Each adapter synthesizes an :class:`ExecutionIdentity` from the run-scoped tool
call context. Until the executor enriches that context (later work package),
``tool_call_id`` / ``request_id`` fall back to stable placeholders; the synthesis
already reads the real values the moment they are provided, so no adapter change
is needed then.
"""

from __future__ import annotations

import uuid
from typing import Any

from agent_driver.contracts.execution import (
    ExecutionCommandRequest,
    ExecutionIdentity,
    ExecutionReadRequest,
    ExecutionWriteRequest,
)
from agent_driver.execution.protocol import ExecutionBackend
from agent_driver.tools.context import (
    current_tool_attempt_epoch,
    get_tool_call_context,
)

# The tool's own size guard runs AFTER the routed read (see read_text_routed), so
# the adapter must not pre-empt it with the backend's max_bytes. A very large
# bound keeps the backend's guard inert on this path.
_UNBOUNDED_READ_BYTES = 1 << 62


def identity_from_context(backend_id: str) -> ExecutionIdentity:
    """Build an execution identity from the run-scoped tool-call context.

    Missing fields fall back to non-empty placeholders so the contract's
    ``min_length=1`` invariants always hold. ``request_id`` defaults to a fresh
    idempotency key per call.
    """
    ctx = get_tool_call_context()
    return ExecutionIdentity(
        backend_id=backend_id,
        run_id=ctx.get("run_id") or "unknown-run",
        attempt_id=ctx.get("attempt_id") or str(current_tool_attempt_epoch()),
        tool_call_id=ctx.get("tool_call_id") or "unbound",
        request_id=ctx.get("request_id") or uuid.uuid4().hex,
    )


class BackendCommandRunner:
    """Presents an ``ExecutionBackend`` as the legacy ``AsyncCommandRunner``."""

    def __init__(self, backend: ExecutionBackend) -> None:
        self._backend = backend

    async def run_command(
        self, command: str, *, cwd: str, timeout_seconds: float
    ) -> dict[str, Any]:
        request = ExecutionCommandRequest(
            identity=identity_from_context(self._backend.backend_id),
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_chars=0,
        )
        result = await self._backend.run_command(request)
        # Same shape the local executor returns; the shell tool applies its own
        # display truncation downstream.
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "exit_code": result.exit_code,
        }


class BackendFileIO:
    """Presents an ``ExecutionBackend`` as the legacy ``AsyncFileIO``."""

    def __init__(self, backend: ExecutionBackend) -> None:
        self._backend = backend

    async def read_text(self, path: str) -> str:
        request = ExecutionReadRequest(
            identity=identity_from_context(self._backend.backend_id),
            path=path,
            max_bytes=_UNBOUNDED_READ_BYTES,
        )
        result = await self._backend.read_text(request)
        return result.content

    async def write_text(self, path: str, content: str) -> None:
        request = ExecutionWriteRequest(
            identity=identity_from_context(self._backend.backend_id),
            path=path,
            content=content,
        )
        await self._backend.write_text(request)


__all__ = [
    "BackendCommandRunner",
    "BackendFileIO",
    "identity_from_context",
]
