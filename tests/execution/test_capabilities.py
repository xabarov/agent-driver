"""EPIC-02 Work Package A — capability snapshot, brief, requirement routing."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution import (
    CapabilityName as CN,
    CapabilityState as CS,
    CapabilityStatus,
    ExecutionCapabilitySnapshot,
    ProgramInfo,
    ToolExecutionRequirement,
)


def _snap(**caps):
    return ExecutionCapabilitySnapshot(
        backend_id="b",
        environment_revision="rev1",
        capabilities={k: CapabilityStatus(state=v) for k, v in caps.items()},
    )


# --------------------------------------------------------------------------- #
# snapshot semantics
# --------------------------------------------------------------------------- #
def test_status_of_defaults_unknown_never_supported():
    snap = _snap(command=CS.SUPPORTED)
    assert snap.status_of(CN.COMMAND).state is CS.SUPPORTED
    # unreported capability is UNKNOWN, not SUPPORTED
    assert snap.status_of(CN.RECONNECT).state is CS.UNKNOWN


def test_cache_key_includes_backend_env_and_lease():
    a = ExecutionCapabilitySnapshot(backend_id="b", environment_revision="r1")
    b = ExecutionCapabilitySnapshot(
        backend_id="b", environment_revision="r1", lease_generation="g2"
    )
    assert a.cache_key() != b.cache_key()
    assert a.cache_key() == "b|r1|-"


# --------------------------------------------------------------------------- #
# check_requirement
# --------------------------------------------------------------------------- #
def test_hard_requirement_satisfied_only_when_supported():
    snap = _snap(command=CS.SUPPORTED, file_write=CS.SUPPORTED)
    req = ToolExecutionRequirement(required=(CN.COMMAND, CN.FILE_WRITE))
    check = ex.check_requirement(snap, req)
    assert check.satisfied and not check.unmet


@pytest.mark.parametrize("state", [CS.UNKNOWN, CS.UNSUPPORTED, CS.DEGRADED])
def test_hard_requirement_fails_closed_for_non_supported(state):
    snap = _snap(command=state)
    check = ex.check_requirement(snap, ToolExecutionRequirement(required=(CN.COMMAND,)))
    assert not check.satisfied
    assert CN.COMMAND in check.unmet
    assert state.value in (check.reason or "")


def test_soft_requirement_never_blocks_but_reports():
    snap = _snap(command=CS.UNKNOWN)
    check = ex.check_requirement(
        snap, ToolExecutionRequirement(required=(CN.COMMAND,), hard=False)
    )
    assert check.satisfied  # soft never blocks
    assert check.unmet == (CN.COMMAND,)  # but still surfaced


def test_empty_requirement_is_satisfied():
    assert ex.check_requirement(_snap(), ToolExecutionRequirement()).satisfied


# --------------------------------------------------------------------------- #
# derive_environment_brief
# --------------------------------------------------------------------------- #
def test_brief_lists_supported_and_degraded_sorted_with_revision():
    snap = ExecutionCapabilitySnapshot(
        backend_id="b",
        environment_revision="rev9",
        digest="dig1",
        capabilities={
            CN.FILE_WRITE: CapabilityStatus(state=CS.SUPPORTED),
            CN.COMMAND: CapabilityStatus(state=CS.SUPPORTED),
            CN.OUTPUT: CapabilityStatus(state=CS.DEGRADED),
            CN.RECONNECT: CapabilityStatus(state=CS.UNKNOWN),
        },
        programs=(ProgramInfo(name="python", version="3.12"),),
        limitations=("no network",),
    )
    brief = ex.derive_environment_brief(snap)
    assert brief.supported == ("command", "file_write")  # sorted, unknown omitted
    assert brief.degraded == ("output",)
    assert brief.capability_revision == "dig1"  # digest preferred over revision
    assert brief.programs == ("python 3.12",)
    assert brief.limitations == ("no network",)
    assert brief.truncated is False


def test_brief_truncates_deterministically_when_over_budget():
    snap = ExecutionCapabilitySnapshot(
        backend_id="b",
        environment_revision="rev1",
        programs=tuple(ProgramInfo(name=f"prog{i}" * 5) for i in range(20)),
        limitations=tuple(f"limitation number {i} is quite verbose" for i in range(10)),
    )
    brief = ex.derive_environment_brief(snap, max_chars=80)
    assert brief.truncated is True
    total = sum(len(x) for x in brief.programs + brief.limitations)
    assert total <= 80


# --------------------------------------------------------------------------- #
# resolve_capability_snapshot (fail-safe handshake)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resolve_from_capability_aware_backend():
    snap = await ex.resolve_capability_snapshot(ex.FakeExecutionBackend())
    assert snap.status_of(CN.COMMAND).state is CS.SUPPORTED


@pytest.mark.asyncio
async def test_resolve_from_minimal_backend_is_unknown():
    class Minimal:
        backend_id = "min"

        async def run_command(self, request): ...
        async def read_text(self, request): ...
        async def write_text(self, request): ...

    snap = await ex.resolve_capability_snapshot(Minimal())
    assert snap.backend_id == "min"
    assert snap.environment_revision == "unknown"
    assert snap.status_of(CN.COMMAND).state is CS.UNKNOWN


@pytest.mark.asyncio
async def test_resolve_fails_safe_on_handshake_error():
    backend = ex.FakeExecutionBackend(raise_on_capabilities=True)
    snap = await ex.resolve_capability_snapshot(backend)
    assert snap.environment_revision == "unknown"  # fell back to UNKNOWN


# --------------------------------------------------------------------------- #
# backend capabilities() truthfulness + protocol membership
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_local_backend_reports_command_and_file_supported():
    snap = await ex.LocalExecutionBackend().capabilities()
    assert snap.status_of(CN.COMMAND).state is CS.SUPPORTED
    assert snap.status_of(CN.FILE_READ).state is CS.SUPPORTED
    assert snap.status_of(CN.RECONNECT).state is CS.UNKNOWN


@pytest.mark.asyncio
async def test_composite_reports_present_and_absent_halves_truthfully():
    class _Runner:
        async def run_command(self, command, *, cwd, timeout_seconds):
            return {"stdout": "", "stderr": "", "timed_out": False, "exit_code": 0}

    # command-only composite: file IO is UNSUPPORTED (observed absence)
    be = ex.CompositeExecutionBackend(command_runner=_Runner(), file_io=None)
    snap = await be.capabilities()
    assert snap.status_of(CN.COMMAND).state is CS.SUPPORTED
    assert snap.status_of(CN.FILE_READ).state is CS.UNSUPPORTED


def test_capability_aware_backends_satisfy_optional_protocol():
    assert isinstance(ex.LocalExecutionBackend(), ex.CapabilityAwareBackend)
    assert isinstance(ex.FakeExecutionBackend(), ex.CapabilityAwareBackend)

    class Minimal:
        backend_id = "m"

        async def run_command(self, request): ...
        async def read_text(self, request): ...
        async def write_text(self, request): ...

    # minimal EPIC-01 backend is a plain ExecutionBackend, not capability-aware
    assert isinstance(Minimal(), ex.ExecutionBackend)
    assert not isinstance(Minimal(), ex.CapabilityAwareBackend)
