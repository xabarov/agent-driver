"""Execution backends: run the built-in bash/read/write in a host environment.

The `agent_driver.execution` building block is a backend-neutral seam. A host
injects a supported `ExecutionBackend`, and the built-in `bash`/`read`/`write`
byte transfer flows through it — a prepared local workspace or, later, a remote
one — WITHOUT changing the agent loop or governance order. The model can never
select the backend; a backend method is only reached from inside an
already-authorized tool handler.

This example uses the deterministic in-memory `FakeExecutionBackend` (the same
one the tests use) so it runs offline, then shows the reference
`LocalExecutionBackend`. Inject via `RunnerConfig(execution_backend=...)` for a
process-wide default, or per run via `Agent.run(execution_backend=...)`.

    python examples/cookbook/20_execution_backend.py
"""

from __future__ import annotations

import asyncio

from agent_driver.execution import (
    CommandOutcome,
    ExecutionCommandRequest,
    ExecutionIdentity,
    ExecutionReadRequest,
    ExecutionWriteRequest,
    FakeExecutionBackend,
    LocalExecutionBackend,
)


def _identity(backend_id: str) -> ExecutionIdentity:
    # In a real run the runtime supplies this from the run/attempt/tool-call
    # context after governance allows the call; here we build one directly.
    return ExecutionIdentity(
        backend_id=backend_id,
        run_id="run_1",
        attempt_id="attempt_1",
        tool_call_id="call_1",
        request_id="call_1",
    )


async def main() -> None:
    # 1) A deterministic fake backend: scripted command output, in-memory files.
    fake = FakeExecutionBackend(
        commands={"echo hi": CommandOutcome(stdout="routed through fake\n")},
    )
    cmd = await fake.run_command(
        ExecutionCommandRequest(
            identity=_identity("fake"),
            command="echo hi",
            cwd="/work",
            timeout_seconds=5,
            max_output_chars=4000,
        )
    )
    print("fake command:", cmd.terminal_state.value, repr(cmd.stdout))

    await fake.write_text(
        ExecutionWriteRequest(
            identity=_identity("fake"), path="/work/note.txt", content="hello"
        )
    )
    read = await fake.read_text(
        ExecutionReadRequest(
            identity=_identity("fake"), path="/work/note.txt", max_bytes=10_000
        )
    )
    print("fake file round-trip:", repr(read.content), read.size_bytes, "bytes")

    # Every request is recorded — a test can assert what the runtime asked for.
    print("recorded commands:", [c.command for c in fake.command_calls])

    # 2) The reference local backend runs a real subprocess (unchanged default
    #    behavior). A non-zero exit is still a COMPLETED execution.
    local = LocalExecutionBackend()
    res = await local.run_command(
        ExecutionCommandRequest(
            identity=_identity("local"),
            command="printf 'from local backend'",
            cwd="/",
            timeout_seconds=5,
            max_output_chars=4000,
        )
    )
    print("local command:", res.terminal_state.value, repr(res.stdout))

    # To wire a backend into a real agent, pass it to the runner/agent:
    #
    #     from agent_driver.runtime import RunnerConfig
    #     config = RunnerConfig(execution_backend=my_backend)   # process default
    #     # ...or per run:
    #     await agent.run(run_input, execution_backend=my_backend)
    #
    # With no backend configured, bash/read/write keep their local subprocess +
    # local-disk behavior exactly as before.


if __name__ == "__main__":
    asyncio.run(main())
