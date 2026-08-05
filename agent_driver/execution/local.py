"""The default local execution backend (EPIC-01).

Faithful reproduction of Agent Driver's built-in local behavior: a subprocess
for commands and local-disk bytes for text read/write. Installing it changes
nothing observable — it is the compatibility reference every other backend is
measured against. Path resolution / workspace jailing happens ABOVE this, in the
tool handler, before a request ever reaches the backend.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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
from agent_driver.execution.errors import OutputLimitExceededError


class LocalExecutionBackend:
    """Runs commands via a local subprocess and text IO against local disk.

    Mirrors ``_execute_bash`` and the filesystem tools' local paths exactly:
    output is NOT truncated here (the tool applies its own display bound), a
    non-zero exit is still a COMPLETED execution, and only a genuine timeout
    yields TIMED_OUT.
    """

    def __init__(self, backend_id: str = "local") -> None:
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    async def run_command(
        self, request: ExecutionCommandRequest
    ) -> ExecutionCommandResult:
        proc = await asyncio.create_subprocess_shell(
            request.command,
            cwd=request.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            raw_stdout, raw_stderr = await asyncio.wait_for(
                proc.communicate(), timeout=request.timeout_seconds
            )
        except (TimeoutError, asyncio.TimeoutError):
            timed_out = True
            proc.kill()
            raw_stdout, raw_stderr = await proc.communicate()
        exit_code = int(proc.returncode) if proc.returncode is not None else 1
        return ExecutionCommandResult(
            identity=request.identity,
            terminal_state=(
                ExecutionTerminalState.TIMED_OUT
                if timed_out
                else ExecutionTerminalState.COMPLETED
            ),
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=raw_stdout.decode("utf-8", errors="replace"),
            stderr=raw_stderr.decode("utf-8", errors="replace"),
            truncated=False,
            bounds=ExecutionBounds(max_output_chars=request.max_output_chars),
        )

    async def read_text(self, request: ExecutionReadRequest) -> ExecutionReadResult:
        path = Path(request.path)
        size = path.stat().st_size
        if size > request.max_bytes:
            raise OutputLimitExceededError(
                f"file exceeds max_bytes ({size}>{request.max_bytes})"
            )
        with path.open("r", encoding="utf-8", newline="") as handle:
            content = handle.read()
        return ExecutionReadResult(
            identity=request.identity,
            path=request.path,
            content=content,
            size_bytes=len(content.encode("utf-8")),
        )

    async def write_text(
        self, request: ExecutionWriteRequest
    ) -> ExecutionWriteResult:
        Path(request.path).write_text(request.content, encoding="utf-8")
        return ExecutionWriteResult(
            identity=request.identity,
            path=request.path,
            bytes_written=len(request.content.encode("utf-8")),
        )


__all__ = ["LocalExecutionBackend"]
