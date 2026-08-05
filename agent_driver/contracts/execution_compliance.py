"""Public contracts for the backend compatibility report (EPIC-05).

A backend author runs the deterministic compliance suite and gets a truthful,
redaction-safe report of exactly which capabilities and guarantees their adapter
PROVED — never self-declared. The report distinguishes passed / failed /
unsupported / skipped / stale / no_claim, and a claimed-but-unproved mandatory
capability is a failure, not a pass.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from agent_driver.contracts.base import ContractModel

EXECUTION_COMPLIANCE_SCHEMA_VERSION = "agent_driver.execution.compliance.v1"

_MAX_DETAIL_CHARS = 500


class ComplianceStatus(str, Enum):
    """Outcome of one compliance check. ``NO_CLAIM`` means the backend does not
    advertise the capability (correctly absent); ``UNSUPPORTED`` means it was
    exercised and the backend truthfully reported it cannot do it; ``SKIPPED``
    means the check was not run. None of these ever count as ``PASSED``."""

    PASSED = "passed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    SKIPPED = "skipped"
    STALE = "stale"
    NO_CLAIM = "no_claim"


class ComplianceGroup(str, Enum):
    """The required compliance groups (one per guarantee family)."""

    CONTRACT = "contract"
    GOVERNANCE = "governance"
    IDENTITY = "identity"
    LEASE = "lease"
    WORKSPACE = "workspace"
    OUTPUT = "output"
    EVENTS = "events"
    DISPATCH = "dispatch"
    CONTROL = "control"
    RECOVERY = "recovery"
    TEARDOWN = "teardown"
    CONCURRENCY = "concurrency"
    TIMING = "timing"


class ComplianceCheck(ContractModel):
    """One deterministic scenario result. ``detail`` is bounded and redaction-safe
    (no raw secrets/unbounded output); ``evidence_digest`` is a content hash of
    the observed evidence, not the evidence itself."""

    group: ComplianceGroup
    scenario: str = Field(min_length=1)
    status: ComplianceStatus
    detail: str = Field(default="", max_length=_MAX_DETAIL_CHARS)
    evidence_digest: str | None = None


class ComplianceReport(ContractModel):
    """The full, versioned, redaction-safe compatibility report. A backend
    advertises only the capabilities proved for THIS ``contract_version`` +
    ``environment_revision``."""

    schema_version: str = EXECUTION_COMPLIANCE_SCHEMA_VERSION
    contract_version: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    environment_revision: str = Field(min_length=1)
    checks: tuple[ComplianceCheck, ...] = ()

    def _count(self, status: ComplianceStatus) -> int:
        return sum(1 for c in self.checks if c.status is status)

    @property
    def passed(self) -> int:
        return self._count(ComplianceStatus.PASSED)

    @property
    def failed(self) -> int:
        return self._count(ComplianceStatus.FAILED)

    @property
    def ok(self) -> bool:
        """True when NO check failed (skipped/unsupported/no_claim don't fail)."""
        return self.failed == 0

    def group_status(self, group: ComplianceGroup) -> ComplianceStatus:
        """The worst status observed in a group (FAILED dominates), or NO_CLAIM
        when the group produced no checks."""
        statuses = [c.status for c in self.checks if c.group is group]
        if not statuses:
            return ComplianceStatus.NO_CLAIM
        for worst in (
            ComplianceStatus.FAILED,
            ComplianceStatus.STALE,
            ComplianceStatus.UNSUPPORTED,
            ComplianceStatus.SKIPPED,
            ComplianceStatus.PASSED,
        ):
            if worst in statuses:
                return worst
        return ComplianceStatus.NO_CLAIM


__all__ = [
    "EXECUTION_COMPLIANCE_SCHEMA_VERSION",
    "ComplianceStatus",
    "ComplianceGroup",
    "ComplianceCheck",
    "ComplianceReport",
]
