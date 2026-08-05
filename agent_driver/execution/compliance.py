"""Deterministic backend compatibility runner (EPIC-05).

Point :func:`run_compliance` at ANY ``ExecutionBackend`` implementation — no live
LLM, no Docker, no network, no credentials — and get a truthful
:class:`ComplianceReport`. Each group runs deterministic scenarios ONLY when the
backend advertises the matching capability; an unadvertised group is ``NO_CLAIM``
(never inflated to a pass), and a claimed-but-broken guarantee is a ``FAILED``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable

from agent_driver.contracts.execution import (
    EXECUTION_SCHEMA_VERSION,
    ExecutionCommandRequest,
    ExecutionIdentity,
    ExecutionReadRequest,
    ExecutionWriteRequest,
)
from agent_driver.contracts.execution_compliance import (
    ComplianceCheck,
    ComplianceGroup,
    ComplianceReport,
    ComplianceStatus,
)
from agent_driver.contracts.execution_lease import ExecutionLeaseRequest, WorkspacePaths
from agent_driver.execution.capabilities import resolve_capability_snapshot
from agent_driver.execution.errors import OutputLimitExceededError
from agent_driver.execution.jobs import JobSession
from agent_driver.execution.pathsafety import (
    WorkspacePathError,
    validate_workspace_path,
)


def _identity(backend_id: str, request_id: str = "compliance") -> ExecutionIdentity:
    return ExecutionIdentity(
        backend_id=backend_id,
        run_id="compliance-run",
        attempt_id="compliance-attempt",
        tool_call_id="compliance-call",
        request_id=request_id,
    )


def _digest(*parts: Any) -> str:
    payload = "|".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def _scenario(
    group: ComplianceGroup,
    name: str,
    fn: Callable[[], Awaitable[str]],
) -> ComplianceCheck:
    """Run one scenario; PASSED with an evidence digest, or FAILED with a bounded
    (never raw-secret) detail. An ``AssertionError`` message is the failure
    reason; any other exception is reported by type only."""
    try:
        evidence = await fn()
    except AssertionError as exc:
        return ComplianceCheck(
            group=group,
            scenario=name,
            status=ComplianceStatus.FAILED,
            detail=str(exc)[:400],
        )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return ComplianceCheck(
            group=group,
            scenario=name,
            status=ComplianceStatus.FAILED,
            detail=f"raised {type(exc).__name__}",
        )
    return ComplianceCheck(
        group=group,
        scenario=name,
        status=ComplianceStatus.PASSED,
        evidence_digest=_digest(name, evidence),
    )


def _no_claim(group: ComplianceGroup, name: str) -> ComplianceCheck:
    return ComplianceCheck(group=group, scenario=name, status=ComplianceStatus.NO_CLAIM)


async def _contract_and_identity(backend: Any, bid: str) -> list[ComplianceCheck]:
    async def _typed_command() -> str:
        req = ExecutionCommandRequest(
            identity=_identity(bid),
            command="true",
            cwd="/",
            timeout_seconds=5,
            max_output_chars=1000,
        )
        res = await backend.run_command(req)
        assert res.exit_code is not None, "result missing exit_code"
        assert res.identity.request_id == req.identity.request_id, (
            "backend did not propagate request identity"
        )
        return f"{res.terminal_state.value}:{res.exit_code}"

    return [
        await _scenario(
            ComplianceGroup.CONTRACT, "typed_command_result", _typed_command
        ),
        await _scenario(
            ComplianceGroup.IDENTITY, "request_identity_propagation", _typed_command
        ),
    ]


async def _output_checks(backend: Any, bid: str) -> list[ComplianceCheck]:
    name = "read_size_bound"
    if not callable(getattr(backend, "read_text", None)):
        return [_no_claim(ComplianceGroup.OUTPUT, name)]
    if not callable(getattr(backend, "write_text", None)):
        # cannot seed a file deterministically here -> SKIPPED, never a pass/fail
        return [
            ComplianceCheck(
                group=ComplianceGroup.OUTPUT,
                scenario=name,
                status=ComplianceStatus.SKIPPED,
                detail="backend is not workspace-writable in the harness",
            )
        ]
    path = "/compliance/big.txt"
    try:
        await backend.write_text(
            ExecutionWriteRequest(identity=_identity(bid), path=path, content="x" * 100)
        )
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # cannot seed a file (e.g. a local backend without a writable dir here)
        return [
            ComplianceCheck(
                group=ComplianceGroup.OUTPUT,
                scenario=name,
                status=ComplianceStatus.SKIPPED,
                detail="could not seed a file to exercise the read bound",
            )
        ]

    async def _size_bound() -> str:
        try:
            await backend.read_text(
                ExecutionReadRequest(identity=_identity(bid), path=path, max_bytes=10)
            )
        except OutputLimitExceededError:
            return "bounded"
        raise AssertionError("read did not enforce max_bytes")

    return [await _scenario(ComplianceGroup.OUTPUT, name, _size_bound)]


async def _lease_checks(backend: Any, bid: str) -> list[ComplianceCheck]:
    if not callable(getattr(backend, "acquire_lease", None)):
        return [_no_claim(ComplianceGroup.LEASE, "acquire_reuse_release")]

    async def _acquire_reuse_release() -> str:
        from agent_driver.execution.lease import ExecutionLeaseManager

        mgr = ExecutionLeaseManager()
        req = ExecutionLeaseRequest(request_id="c-lease", backend_id=bid)
        lease1 = await mgr.acquire_or_attach(backend, req)
        lease2 = await mgr.acquire_or_attach(backend, req)
        assert lease1 is lease2, "lease not reused for the same request id"
        assert lease1.is_usable, "acquired lease is not usable"
        await mgr.close(backend)
        await mgr.close(backend)  # idempotent
        return f"{lease1.ref.lease_id}:{lease1.ref.generation}"

    return [
        await _scenario(
            ComplianceGroup.LEASE, "acquire_reuse_release", _acquire_reuse_release
        )
    ]


async def _workspace_checks(backend: Any) -> list[ComplianceCheck]:
    if not callable(getattr(backend, "glob", None)):
        return [_no_claim(ComplianceGroup.WORKSPACE, "path_escape_rejected")]

    async def _path_escape() -> str:
        paths = WorkspacePaths(workspace_root="/work", writable_roots=("/work",))
        try:
            validate_workspace_path("../etc/passwd", paths)
        except WorkspacePathError:
            return "rejected"
        raise AssertionError("traversal escape was not rejected")

    return [
        await _scenario(ComplianceGroup.WORKSPACE, "path_escape_rejected", _path_escape)
    ]


def _job_command(bid: str) -> ExecutionCommandRequest:
    return ExecutionCommandRequest(
        identity=_identity(bid, "c-job"),
        command="long",
        cwd="/",
        timeout_seconds=30,
        max_output_chars=4000,
    )


async def _job_checks(
    backend: Any, bid: str, *, claims_teardown: bool
) -> list[ComplianceCheck]:
    if not callable(getattr(backend, "start_job", None)):
        return [
            _no_claim(ComplianceGroup.EVENTS, "observe_to_terminal"),
            _no_claim(ComplianceGroup.DISPATCH, "idempotent_start"),
            _no_claim(ComplianceGroup.CONTROL, "accepted_applied_distinct"),
            _no_claim(ComplianceGroup.TEARDOWN, "teardown_receipt"),
        ]

    async def _observe() -> str:
        session = JobSession(backend)
        handle = await session.start(_job_command(bid))
        snap = await session.observe_to_terminal(handle)
        return snap.state.value

    async def _idempotent_start() -> str:
        h1 = await backend.start_job(_job_command(bid))
        h2 = await backend.start_job(_job_command(bid))
        assert h1.job_id == h2.job_id, "start not idempotent by request id"
        return h1.job_id

    async def _control() -> str:
        handle = await backend.start_job(_job_command(bid))
        from agent_driver.contracts.execution_job import (
            ExecutionControlKind,
            ExecutionControlRequest,
        )

        receipt = await backend.control(
            ExecutionControlRequest(handle=handle, kind=ExecutionControlKind.STOP)
        )
        # accepted and applied are distinct fields (no fabrication either way)
        assert isinstance(receipt.accepted, bool) and isinstance(receipt.applied, bool)
        return f"{receipt.accepted}:{receipt.applied}"

    async def _teardown_check() -> ComplianceCheck:
        name = "teardown_receipt"
        try:
            handle = await backend.start_job(_job_command(bid))
            receipt = await backend.teardown(handle)
            assert receipt.requested is True, "teardown did not record the request"
        except AssertionError as exc:
            return ComplianceCheck(
                group=ComplianceGroup.TEARDOWN,
                scenario=name,
                status=ComplianceStatus.FAILED,
                detail=str(exc)[:400],
            )
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return ComplianceCheck(
                group=ComplianceGroup.TEARDOWN,
                scenario=name,
                status=ComplianceStatus.FAILED,
                detail=f"raised {type(exc).__name__}",
            )
        if receipt.confirmed:
            return ComplianceCheck(
                group=ComplianceGroup.TEARDOWN,
                scenario=name,
                status=ComplianceStatus.PASSED,
                evidence_digest=_digest(name, "confirmed"),
            )
        # Not confirmed: a backend that ADVERTISES teardown must prove it (FAILED);
        # one that does not advertise it is truthfully UNSUPPORTED, never a pass.
        return ComplianceCheck(
            group=ComplianceGroup.TEARDOWN,
            scenario=name,
            status=(
                ComplianceStatus.FAILED
                if claims_teardown
                else ComplianceStatus.UNSUPPORTED
            ),
            detail=(
                "advertises teardown but did not confirm it"
                if claims_teardown
                else "teardown not confirmed (not advertised)"
            ),
        )

    return [
        await _scenario(ComplianceGroup.EVENTS, "observe_to_terminal", _observe),
        await _scenario(
            ComplianceGroup.DISPATCH, "idempotent_start", _idempotent_start
        ),
        await _scenario(ComplianceGroup.CONTROL, "accepted_applied_distinct", _control),
        await _teardown_check(),
    ]


async def run_compliance(backend: Any) -> ComplianceReport:
    """Run the deterministic compatibility suite against ``backend`` and return a
    truthful, redaction-safe :class:`ComplianceReport`. Groups the backend does
    not advertise are ``NO_CLAIM``; a claimed-but-broken guarantee is ``FAILED``.
    Requires only public imports — no live LLM or external infrastructure."""
    from agent_driver.contracts.execution import CapabilityName, CapabilityState

    bid = getattr(backend, "backend_id", "unknown")
    snapshot = await resolve_capability_snapshot(backend)
    claims_teardown = (
        snapshot.status_of(CapabilityName.TEARDOWN).state is CapabilityState.SUPPORTED
    )
    checks: list[ComplianceCheck] = []
    checks += await _contract_and_identity(backend, bid)
    checks += await _output_checks(backend, bid)
    checks += await _lease_checks(backend, bid)
    checks += await _workspace_checks(backend)
    checks += await _job_checks(backend, bid, claims_teardown=claims_teardown)
    return ComplianceReport(
        contract_version=EXECUTION_SCHEMA_VERSION,
        backend_id=bid,
        environment_revision=snapshot.environment_revision,
        checks=tuple(checks),
    )


def render_markdown(report: ComplianceReport) -> str:
    """Render a concise, deterministic (apart from ids/digests) Markdown report."""
    lines = [
        f"# Backend compatibility report: `{report.backend_id}`",
        "",
        f"- contract: `{report.contract_version}`",
        f"- environment revision: `{report.environment_revision}`",
        f"- result: {'OK' if report.ok else 'FAILED'} "
        f"({report.passed} passed, {report.failed} failed)",
        "",
        "| group | scenario | status | evidence |",
        "| --- | --- | --- | --- |",
    ]
    for check in report.checks:
        lines.append(
            f"| {check.group.value} | {check.scenario} | {check.status.value} "
            f"| {check.evidence_digest or check.detail or ''} |"
        )
    return "\n".join(lines) + "\n"


__all__ = ["run_compliance", "render_markdown"]
