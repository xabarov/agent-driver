"""EPIC-05 — deterministic backend compatibility runner + report."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution import (
    CapabilityName,
    CapabilityState,
    CapabilityStatus,
    ExecutionCapabilitySnapshot,
)
from agent_driver.contracts.execution_compliance import (
    ComplianceGroup,
    ComplianceReport,
    ComplianceStatus,
)


def _teardown_capable_snapshot():
    supported = CapabilityStatus(state=CapabilityState.SUPPORTED)
    return ExecutionCapabilitySnapshot(
        backend_id="fake",
        environment_revision="rev-td",
        capabilities={
            CapabilityName.COMMAND: supported,
            CapabilityName.FILE_READ: supported,
            CapabilityName.FILE_WRITE: supported,
            CapabilityName.TEARDOWN: supported,
        },
    )


def _full_fake(**kw):
    return ex.FakeExecutionBackend(files={"/compliance/big.txt": "x" * 100}, **kw)


# --------------------------------------------------------------------------- #
# scenario 1 + 10 — a full backend passes its claimed subset via public imports
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_full_backend_passes_its_declared_profile():
    report = await ex.run_compliance(_full_fake())
    assert report.ok
    assert report.failed == 0
    assert report.group_status(ComplianceGroup.CONTRACT) is ComplianceStatus.PASSED
    assert report.group_status(ComplianceGroup.LEASE) is ComplianceStatus.PASSED
    assert report.group_status(ComplianceGroup.EVENTS) is ComplianceStatus.PASSED
    # not advertised -> UNSUPPORTED, never inflated to PASSED (scenario 5)
    assert report.group_status(ComplianceGroup.TEARDOWN) is ComplianceStatus.UNSUPPORTED


# --------------------------------------------------------------------------- #
# scenario 5 — a minimal backend's unclaimed groups are NO_CLAIM, not passes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_minimal_backend_unclaimed_groups_are_no_claim():
    class Minimal:
        backend_id = "min"

        async def run_command(self, r):
            from agent_driver.contracts.execution import (
                ExecutionBounds,
                ExecutionCommandResult,
                ExecutionTerminalState,
            )

            return ExecutionCommandResult(
                identity=r.identity,
                terminal_state=ExecutionTerminalState.COMPLETED,
                exit_code=0,
                bounds=ExecutionBounds(max_output_chars=r.max_output_chars),
            )

        async def read_text(self, r):
            raise FileNotFoundError(r.path)

        async def write_text(self, r): ...

    report = await ex.run_compliance(Minimal())
    assert report.group_status(ComplianceGroup.CONTRACT) is ComplianceStatus.PASSED
    assert report.group_status(ComplianceGroup.IDENTITY) is ComplianceStatus.PASSED
    assert report.group_status(ComplianceGroup.LEASE) is ComplianceStatus.NO_CLAIM
    assert report.group_status(ComplianceGroup.EVENTS) is ComplianceStatus.NO_CLAIM
    assert report.group_status(ComplianceGroup.TEARDOWN) is ComplianceStatus.NO_CLAIM


# --------------------------------------------------------------------------- #
# scenario 2 — claims hard teardown but only acknowledges -> teardown FAILS
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backend_claiming_unproved_teardown_fails_that_group():
    liar = _full_fake(
        capability_snapshot=_teardown_capable_snapshot(), teardown_confirmed=False
    )
    report = await ex.run_compliance(liar)
    assert not report.ok
    assert report.group_status(ComplianceGroup.TEARDOWN) is ComplianceStatus.FAILED


@pytest.mark.asyncio
async def test_backend_that_confirms_teardown_passes_that_group():
    honest = _full_fake(
        capability_snapshot=_teardown_capable_snapshot(), teardown_confirmed=True
    )
    report = await ex.run_compliance(honest)
    assert report.group_status(ComplianceGroup.TEARDOWN) is ComplianceStatus.PASSED


# --------------------------------------------------------------------------- #
# scenario 4 — a broken guarantee is detected deterministically
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_non_idempotent_start_fails_dispatch_group():
    class _NonIdempotent(ex.FakeExecutionBackend):
        _n = 0

        async def start_job(self, request):
            self._n += 1
            handle = await super().start_job(request)
            return handle.model_copy(update={"job_id": f"{handle.job_id}-{self._n}"})

    report = await ex.run_compliance(
        _NonIdempotent(files={"/compliance/big.txt": "x" * 100})
    )
    assert report.group_status(ComplianceGroup.DISPATCH) is ComplianceStatus.FAILED


# --------------------------------------------------------------------------- #
# scenario 8 — report is versioned, redaction-safe, JSON round-trips, renders
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_report_is_versioned_json_safe_and_renders():
    report = await ex.run_compliance(_full_fake())
    assert report.contract_version and report.environment_revision
    # JSON round-trips (checkpoint/report-safe)
    back = ComplianceReport.model_validate_json(report.model_dump_json())
    assert back == report
    # no raw secret-ish content in details (evidence is a digest, not payload)
    for check in report.checks:
        assert "sk-" not in (check.detail or "")
    md = ex.render_markdown(report)
    assert "Backend compatibility report" in md
    assert "| group | scenario | status |" in md


@pytest.mark.asyncio
async def test_compliance_needs_no_live_llm_or_infra():
    # The whole suite runs against an in-memory backend with only public imports.
    report = await ex.run_compliance(ex.FakeExecutionBackend())
    assert isinstance(report, ComplianceReport)


# --------------------------------------------------------------------------- #
# scenario 6 — the built-in LOCAL backend qualifies truthfully
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_local_backend_qualifies_its_declared_profile():
    report = await ex.run_compliance(ex.LocalExecutionBackend())
    assert report.ok  # no FAILED
    # Local proves command + identity...
    assert report.group_status(ComplianceGroup.CONTRACT) is ComplianceStatus.PASSED
    assert report.group_status(ComplianceGroup.IDENTITY) is ComplianceStatus.PASSED
    # ...and truthfully claims none of the remote lifecycle guarantees.
    assert report.group_status(ComplianceGroup.LEASE) is ComplianceStatus.NO_CLAIM
    assert report.group_status(ComplianceGroup.EVENTS) is ComplianceStatus.NO_CLAIM
    assert report.group_status(ComplianceGroup.TEARDOWN) is ComplianceStatus.NO_CLAIM
