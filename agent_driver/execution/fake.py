"""A deterministic in-memory execution backend for tests (EPIC-01).

No subprocess, no disk, no clock: command results are scripted, files live in a
dict, and every request is recorded so a test can assert what the runtime asked
of the backend. Use it to drive the runner through a backend without touching
the host machine.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from agent_driver.contracts.execution import (
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
)
from agent_driver.contracts.execution_lease import (
    ExecutionLease,
    ExecutionLeaseRef,
    ExecutionLeaseRequest,
    LeaseOwnership,
    LeaseState,
    WorkspacePaths,
)
from agent_driver.contracts.execution_workspace import (
    ExecutionDeleteRequest,
    ExecutionDeleteResult,
    ExecutionGlobRequest,
    ExecutionGlobResult,
    ExecutionGrepRequest,
    ExecutionGrepResult,
    ExecutionListRequest,
    ExecutionListResult,
    ExecutionStatRequest,
    ExecutionStatResult,
    GrepMatch,
    WorkspaceEntry,
)
from agent_driver.execution.errors import (
    ExecutionTimeoutError,
    OutputLimitExceededError,
)


def _default_fake_snapshot() -> ExecutionCapabilitySnapshot:
    supported = CapabilityStatus(state=CapabilityState.SUPPORTED)
    return ExecutionCapabilitySnapshot(
        backend_id="fake",
        environment_revision="fake-1",
        capabilities={
            CapabilityName.COMMAND: supported,
            CapabilityName.FILE_READ: supported,
            CapabilityName.FILE_WRITE: supported,
        },
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
    capability_snapshot: ExecutionCapabilitySnapshot = field(
        default_factory=_default_fake_snapshot
    )
    raise_on_capabilities: bool = False
    # Lease scripting (EPIC-03). ``acquire_state`` lets a test force a non-READY
    # lease; ``known_generations`` maps lease_id -> current generation so a stale
    # attach fails closed.
    acquire_state: "LeaseState" = None  # type: ignore[assignment]
    known_generations: dict[str, str] = field(default_factory=dict)
    command_calls: list[ExecutionCommandRequest] = field(default_factory=list)
    read_calls: list[ExecutionReadRequest] = field(default_factory=list)
    write_calls: list[ExecutionWriteRequest] = field(default_factory=list)
    lease_acquires: list[ExecutionLeaseRequest] = field(default_factory=list)
    lease_attaches: list[ExecutionLeaseRef] = field(default_factory=list)
    lease_releases: list[ExecutionLeaseRef] = field(default_factory=list)
    lease_detaches: list[ExecutionLeaseRef] = field(default_factory=list)
    _lease_seq: int = 0

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

    async def capabilities(self) -> ExecutionCapabilitySnapshot:
        """Return the scripted snapshot (or raise to simulate a handshake fault
        so the resolver's fail-safe path can be exercised)."""
        if self.raise_on_capabilities:
            raise ExecutionTimeoutError("fake capability handshake timeout")
        return self.capability_snapshot

    # -- lease lifecycle (EPIC-03) ---------------------------------------- #
    def _new_lease(
        self, *, lease_id: str, generation: str, ownership: LeaseOwnership
    ) -> ExecutionLease:
        state = self.acquire_state or LeaseState.READY
        ref = ExecutionLeaseRef(
            lease_id=lease_id,
            generation=generation,
            backend_id=self.backend_id,
            ownership=ownership,
        )
        return ExecutionLease(
            ref=ref,
            state=state,
            paths=WorkspacePaths(workspace_root="/work", writable_roots=("/work",)),
            capabilities=self.capability_snapshot,
        )

    async def acquire_lease(self, request: ExecutionLeaseRequest) -> ExecutionLease:
        self.lease_acquires.append(request)
        if request.attach_ref is not None:
            return await self.attach_lease(request.attach_ref)
        self._lease_seq += 1
        lease_id = f"lease-{self._lease_seq}"
        generation = "gen-1"
        self.known_generations[lease_id] = generation
        return self._new_lease(
            lease_id=lease_id, generation=generation, ownership=request.ownership
        )

    async def attach_lease(self, ref: ExecutionLeaseRef) -> ExecutionLease:
        self.lease_attaches.append(ref)
        current = self.known_generations.get(ref.lease_id)
        if current is None or current != ref.generation:
            # unknown or stale generation -> fail closed with an EXPIRED lease
            return self._new_lease(
                lease_id=ref.lease_id,
                generation=ref.generation,
                ownership=ref.ownership,
            ).model_copy(update={"state": LeaseState.EXPIRED})
        return self._new_lease(
            lease_id=ref.lease_id, generation=ref.generation, ownership=ref.ownership
        )

    async def release_lease(self, ref: ExecutionLeaseRef) -> None:
        self.lease_releases.append(ref)
        self.known_generations.pop(ref.lease_id, None)

    async def detach_lease(self, ref: ExecutionLeaseRef) -> None:
        self.lease_detaches.append(ref)

    # -- workspace operations (EPIC-03 WP-C) ------------------------------ #
    def _under(self, base: str) -> list[str]:
        prefix = base.rstrip("/") + "/"
        return [p for p in self.files if p == base or p.startswith(prefix)]

    async def list_dir(self, request: ExecutionListRequest) -> ExecutionListResult:
        prefix = request.path.rstrip("/") + "/"
        seen: dict[str, WorkspaceEntry] = {}
        for p in self.files:
            if not p.startswith(prefix):
                continue
            rest = p[len(prefix) :]
            if not request.recursive and "/" in rest:
                # only the immediate child directory
                child = prefix + rest.split("/", 1)[0]
                seen.setdefault(child, WorkspaceEntry(path=child, is_dir=True))
            else:
                seen[p] = WorkspaceEntry(
                    path=p, is_dir=False, size_bytes=len(self.files[p].encode())
                )
        entries = tuple(seen.values())[: request.max_entries]
        return ExecutionListResult(
            identity=request.identity,
            entries=entries,
            truncated=len(seen) > request.max_entries,
        )

    async def glob(self, request: ExecutionGlobRequest) -> ExecutionGlobResult:
        prefix = request.base_path.rstrip("/") + "/"
        matches = [
            p
            for p in sorted(self.files)
            if p.startswith(prefix)
            and fnmatch.fnmatch(p[len(prefix) :], request.pattern)
        ]
        return ExecutionGlobResult(
            identity=request.identity,
            paths=tuple(matches[: request.max_entries]),
            truncated=len(matches) > request.max_entries,
        )

    async def grep(self, request: ExecutionGrepRequest) -> ExecutionGrepResult:
        regex = re.compile(request.pattern)
        prefix = request.base_path.rstrip("/") + "/"
        matches: list[GrepMatch] = []
        for p in sorted(self.files):
            if not (p == request.base_path or p.startswith(prefix)):
                continue
            if request.path_glob and not fnmatch.fnmatch(p, request.path_glob):
                continue
            for i, line in enumerate(self.files[p].splitlines(), start=1):
                if regex.search(line):
                    matches.append(GrepMatch(path=p, line_number=i, line=line))
                    if len(matches) >= request.max_matches:
                        return ExecutionGrepResult(
                            identity=request.identity,
                            matches=tuple(matches),
                            truncated=True,
                        )
        return ExecutionGrepResult(
            identity=request.identity, matches=tuple(matches), truncated=False
        )

    async def stat(self, request: ExecutionStatRequest) -> ExecutionStatResult:
        if request.path in self.files:
            return ExecutionStatResult(
                identity=request.identity,
                path=request.path,
                exists=True,
                is_dir=False,
                size_bytes=len(self.files[request.path].encode()),
            )
        is_dir = any(p.startswith(request.path.rstrip("/") + "/") for p in self.files)
        return ExecutionStatResult(
            identity=request.identity,
            path=request.path,
            exists=is_dir,
            is_dir=is_dir,
        )

    async def delete(self, request: ExecutionDeleteRequest) -> ExecutionDeleteResult:
        if request.recursive:
            targets = self._under(request.path)
        else:
            targets = [request.path] if request.path in self.files else []
        for p in targets:
            self.files.pop(p, None)
        return ExecutionDeleteResult(
            identity=request.identity, path=request.path, deleted=bool(targets)
        )


__all__ = ["FakeExecutionBackend", "CommandOutcome"]
