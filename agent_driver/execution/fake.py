"""A deterministic in-memory execution backend for tests (EPIC-01).

No subprocess, no disk, no clock: command results are scripted, files live in a
dict, and every request is recorded so a test can assert what the runtime asked
of the backend. Use it to drive the runner through a backend without touching
the host machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    ExecutionTimeoutError,
    OutputLimitExceededError,
)


@dataclass
class CommandOutcome:
    """A scripted command result. ``timed_out=True`` yields a TIMED_OUT result."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    truncated: bool = False


@dataclass
class FakeExecutionBackend:
    """In-memory, deterministic backend.

    ``commands`` maps an exact command string to a :class:`CommandOutcome`;
    ``default_outcome`` covers anything else. ``files`` seeds the in-memory FS.
    ``raise_timeout_for`` names commands that must raise
    :class:`ExecutionTimeoutError` (a transport-level timeout, distinct from a
    scripted ``timed_out`` result).
    """

    backend_id: str = "fake"
    commands: dict[str, CommandOutcome] = field(default_factory=dict)
    default_outcome: CommandOutcome = field(default_factory=CommandOutcome)
    files: dict[str, str] = field(default_factory=dict)
    raise_timeout_for: set[str] = field(default_factory=set)
    command_calls: list[ExecutionCommandRequest] = field(default_factory=list)
    read_calls: list[ExecutionReadRequest] = field(default_factory=list)
    write_calls: list[ExecutionWriteRequest] = field(default_factory=list)

    async def run_command(
        self, request: ExecutionCommandRequest
    ) -> ExecutionCommandResult:
        self.command_calls.append(request)
        if request.command in self.raise_timeout_for:
            raise ExecutionTimeoutError(
                f"fake timeout for command: {request.command[:80]}"
            )
        outcome = self.commands.get(request.command, self.default_outcome)
        return ExecutionCommandResult(
            identity=request.identity,
            terminal_state=(
                ExecutionTerminalState.TIMED_OUT
                if outcome.timed_out
                else ExecutionTerminalState.COMPLETED
            ),
            exit_code=outcome.exit_code,
            timed_out=outcome.timed_out,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            truncated=outcome.truncated,
            bounds=ExecutionBounds(max_output_chars=request.max_output_chars),
        )

    async def read_text(self, request: ExecutionReadRequest) -> ExecutionReadResult:
        self.read_calls.append(request)
        if request.path not in self.files:
            raise FileNotFoundError(request.path)
        content = self.files[request.path]
        size = len(content.encode("utf-8"))
        if size > request.max_bytes:
            raise OutputLimitExceededError(
                f"file exceeds max_bytes ({size}>{request.max_bytes})"
            )
        return ExecutionReadResult(
            identity=request.identity,
            path=request.path,
            content=content,
            size_bytes=size,
        )

    async def write_text(self, request: ExecutionWriteRequest) -> ExecutionWriteResult:
        self.write_calls.append(request)
        self.files[request.path] = request.content
        return ExecutionWriteResult(
            identity=request.identity,
            path=request.path,
            bytes_written=len(request.content.encode("utf-8")),
        )


__all__ = ["FakeExecutionBackend", "CommandOutcome"]
