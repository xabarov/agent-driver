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

    # 3) Capabilities (EPIC-02): a backend can report truthful, revisioned facts.
    #    Agent Driver withholds a tool whose HARD requirement is unmet (pre-model)
    #    and denies it before dispatch (pre-dispatch), and shows the model a
    #    bounded environment brief. "unknown" never counts as supported.
    from agent_driver.execution import (
        CapabilityName,
        ToolExecutionRequirement,
        check_requirement,
        derive_environment_brief,
        render_environment_brief_text,
        resolve_capability_snapshot,
    )

    snapshot = await resolve_capability_snapshot(local)
    print("local capabilities:", snapshot.status_of(CapabilityName.COMMAND).state.value)

    needs_reconnect = ToolExecutionRequirement(required=(CapabilityName.RECONNECT,))
    check = check_requirement(snapshot, needs_reconnect)
    print("reconnect-requiring tool satisfied:", check.satisfied, "-", check.reason)

    print("--- environment brief the model would see ---")
    print(render_environment_brief_text(derive_environment_brief(snapshot)))

    # 4) Leases (EPIC-03): a lease-capable backend grants one task-scoped
    #    workspace that spans the whole run. The manager acquires once, reuses,
    #    and releases (runtime-owned) or detaches (host-owned) on every exit.
    from agent_driver.execution import (
        ExecutionLeaseManager,
        ExecutionLeaseRequest,
        LeaseOwnership,
        validate_workspace_path,
    )

    lease_backend = FakeExecutionBackend()  # implements the lease + workspace ops
    manager = ExecutionLeaseManager()
    lease = await manager.acquire_or_attach(
        lease_backend,
        ExecutionLeaseRequest(
            request_id="run-1:lease",
            backend_id=lease_backend.backend_id,
            ownership=LeaseOwnership.RUNTIME_OWNED,
        ),
    )
    print(
        "lease:",
        lease.ref.lease_id,
        lease.state.value,
        "root:",
        lease.paths.workspace_root,
    )
    # Backend-relative path safety (no local disk): traversal is rejected.
    try:
        validate_workspace_path("../escape", lease.paths)
    except Exception as exc:  # WorkspacePathError
        print("path traversal rejected:", type(exc).__name__)
    await manager.close(lease_backend)  # released exactly once
    print("lease released:", [r.phase.value for r in manager.receipts])
    #
    # In a real run, set RunnerConfig(execution_lease_ownership=...) and the
    # runner does acquire/reuse/release for you; every filesystem builtin routes
    # to the leased workspace with this same path safety.

    # 5) Jobs (EPIC-04): a reconnectable long-running operation with bounded
    #    ordered events, generation fencing, and lost-start recovery. JobSession
    #    orchestrates start -> observe -> terminal, tolerating duplicates and
    #    resolving an unknown dispatch as INDETERMINATE (never re-dispatched).
    from agent_driver.execution import (
        ExecutionEvent,
        ExecutionEventCursor,
        ExecutionEventKind,
        ExecutionEventPage,
        ExecutionHandle,
        ExecutionJobState,
        ExecutionTerminalSnapshot,
        JobSession,
    )

    ident = _identity("fake")
    _job_handle = ExecutionHandle(
        job_id="job-call_1",
        idempotency_key="call_1",
        backend_id="fake",
        execution_generation="gen-1",
    )
    job_backend = FakeExecutionBackend(
        job_terminal=ExecutionTerminalSnapshot(
            handle=_job_handle, state=ExecutionJobState.COMPLETED, exit_code=0
        ),
        job_pages=[
            ExecutionEventPage(
                events=(
                    ExecutionEvent(
                        execution_generation="gen-1",
                        sequence=0,
                        kind=ExecutionEventKind.OUTPUT,
                        text="building...",
                    ),
                    ExecutionEvent(
                        execution_generation="gen-1",
                        sequence=1,
                        kind=ExecutionEventKind.OUTPUT,
                        text="done",
                        terminal=True,
                    ),
                ),
                next_cursor=ExecutionEventCursor(
                    job_id="job-call_1", execution_generation="gen-1", last_sequence=1
                ),
                complete=True,
            )
        ],
    )
    session = JobSession(job_backend)
    req = ExecutionCommandRequest(
        identity=ident,
        command="make build",
        cwd="/work",
        timeout_seconds=600,
        max_output_chars=8000,
    )
    handle = await session.start(req)  # idempotent; lost-start resolves by lookup
    lines: list[str] = []
    terminal = await session.observe_to_terminal(
        handle, on_event=lambda e: lines.append(e.text)
    )
    print("job events:", lines, "terminal:", terminal.state.value)
    print("job stage timings:", [t.phase for t in session.timings])

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
