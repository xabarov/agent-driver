"""Backend author kit: implement the protocol and qualify it (EPIC-05).

A backend author needs only PUBLIC ``agent_driver.execution`` imports — no Agent
Driver internals — to implement an execution backend and prove exactly which
guarantees it provides. This example defines a minimal in-memory backend
(command + text read/write, plus a truthful capability snapshot) and runs the
deterministic compatibility suite against it, printing the report. No live LLM,
no Docker, no network, no credentials.

    python examples/cookbook/21_backend_compliance.py
"""

from __future__ import annotations

import asyncio

from agent_driver.execution import (
    CapabilityName,
    CapabilityState,
    CapabilityStatus,
    ExecutionBounds,
    ExecutionCapabilitySnapshot,
    ExecutionCommandRequest,
    ExecutionCommandResult,
    ExecutionReadRequest,
    ExecutionReadResult,
    ExecutionTerminalState,
    ExecutionWriteRequest,
    ExecutionWriteResult,
    OutputLimitExceededError,
    render_markdown,
    run_compliance,
)


class MyBackend:
    """A minimal author-written backend: an in-memory command echo + text FS.

    It advertises exactly what it proves — command and file read/write — and
    nothing else, so the compliance report marks lease/event/teardown groups as
    ``no_claim`` rather than inflating the result.
    """

    backend_id = "my-backend"

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    async def run_command(
        self, request: ExecutionCommandRequest
    ) -> ExecutionCommandResult:
        return ExecutionCommandResult(
            identity=request.identity,  # propagate the caller's identity
            terminal_state=ExecutionTerminalState.COMPLETED,
            exit_code=0,
            stdout=f"ran: {request.command}",
            bounds=ExecutionBounds(max_output_chars=request.max_output_chars),
        )

    async def read_text(self, request: ExecutionReadRequest) -> ExecutionReadResult:
        content = self._files.get(request.path, "")
        size = len(content.encode("utf-8"))
        if size > request.max_bytes:
            raise OutputLimitExceededError(f"file exceeds max_bytes ({size})")
        return ExecutionReadResult(
            identity=request.identity,
            path=request.path,
            content=content,
            size_bytes=size,
        )

    async def write_text(self, request: ExecutionWriteRequest) -> ExecutionWriteResult:
        self._files[request.path] = request.content
        return ExecutionWriteResult(
            identity=request.identity,
            path=request.path,
            bytes_written=len(request.content.encode("utf-8")),
        )

    async def capabilities(self) -> ExecutionCapabilitySnapshot:
        supported = CapabilityStatus(state=CapabilityState.SUPPORTED)
        return ExecutionCapabilitySnapshot(
            backend_id=self.backend_id,
            environment_revision="my-backend-1",
            capabilities={
                CapabilityName.COMMAND: supported,
                CapabilityName.FILE_READ: supported,
                CapabilityName.FILE_WRITE: supported,
            },
        )


async def main() -> None:
    report = await run_compliance(MyBackend())
    print(render_markdown(report), end="")
    print(
        f"\nresult: {'OK' if report.ok else 'FAILED'} "
        f"({report.passed} passed, {report.failed} failed)"
    )


if __name__ == "__main__":
    asyncio.run(main())
