"""Lift a legacy runner/file-IO pair into an ``ExecutionBackend`` (EPIC-01).

Hosts that already provide the pre-existing run-scoped
:class:`~agent_driver.tools.context.AsyncCommandRunner` /
:class:`~agent_driver.tools.context.AsyncFileIO` (notably the ACP adapter, which
runs commands in the editor terminal and routes bytes to the editor buffers) can
be presented AS an ``ExecutionBackend`` without rewriting the host. A capability
that the host did not supply is reported UNSUPPORTED — never silently upgraded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_driver.contracts.execution import (
    ExecutionBounds,
    ExecutionCommandRequest,
    ExecutionCommandResult,
    ExecutionReadRequest,
    ExecutionReadResult,
    ExecutionTerminalState,
    ExecutionWriteRequest,
    ExecutionWriteResult,
)
from agent_driver.execution.errors import (
    BackendProtocolError,
    UnsupportedCapabilityError,
)

if TYPE_CHECKING:
    from agent_driver.tools.context import AsyncCommandRunner, AsyncFileIO


class CompositeExecutionBackend:
    """Adapt a legacy ``AsyncCommandRunner`` + ``AsyncFileIO`` to the protocol.

    Either half may be ``None`` — the corresponding method then raises
    :class:`UnsupportedCapabilityError`. This is the migration bridge that keeps
    ACP working under the new seam.
    """

    def __init__(
        self,
        *,
        command_runner: "AsyncCommandRunner | None" = None,
        file_io: "AsyncFileIO | None" = None,
        backend_id: str = "composite",
    ) -> None:
        self._command_runner = command_runner
        self._file_io = file_io
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    async def run_command(
        self, request: ExecutionCommandRequest
    ) -> ExecutionCommandResult:
        if self._command_runner is None:
            raise UnsupportedCapabilityError(
                "composite backend has no command runner"
            )
        raw = await self._command_runner.run_command(
            request.command,
            cwd=request.cwd,
            timeout_seconds=request.timeout_seconds,
        )
        if not isinstance(raw, dict):
            raise BackendProtocolError(
                "command runner returned a non-dict result"
            )
        timed_out = bool(raw.get("timed_out", False))
        exit_code_raw = raw.get("exit_code", 1)
        try:
            exit_code = int(exit_code_raw)
        except (TypeError, ValueError) as exc:
            raise BackendProtocolError(
                "command runner returned a non-integer exit_code"
            ) from exc
        return ExecutionCommandResult(
            identity=request.identity,
            terminal_state=(
                ExecutionTerminalState.TIMED_OUT
                if timed_out
                else ExecutionTerminalState.COMPLETED
            ),
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=str(raw.get("stdout", "")),
            stderr=str(raw.get("stderr", "")),
            truncated=bool(raw.get("truncated", False)),
            bounds=ExecutionBounds(max_output_chars=request.max_output_chars),
        )

    async def read_text(self, request: ExecutionReadRequest) -> ExecutionReadResult:
        if self._file_io is None:
            raise UnsupportedCapabilityError("composite backend has no file IO")
        content = await self._file_io.read_text(request.path)
        content = str(content)
        return ExecutionReadResult(
            identity=request.identity,
            path=request.path,
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )

    async def write_text(
        self, request: ExecutionWriteRequest
    ) -> ExecutionWriteResult:
        if self._file_io is None:
            raise UnsupportedCapabilityError("composite backend has no file IO")
        await self._file_io.write_text(request.path, request.content)
        return ExecutionWriteResult(
            identity=request.identity,
            path=request.path,
            bytes_written=len(request.content.encode("utf-8")),
        )


__all__ = ["CompositeExecutionBackend"]
