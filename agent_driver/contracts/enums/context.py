"""Context-engineering enums for sessions/artifacts/planning/observations."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class PlanningTodoStatus(StrEnum):
    """Status for one planning/todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlanningModeState(StrEnum):
    """Approval lifecycle for a durable plan artifact."""

    DISABLED = "disabled"
    COLLECTING = "collecting"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PlanningHintLevel(StrEnum):
    """Runtime hint for whether a request should enter planning mode."""

    NONE = "none"
    SUGGESTED = "suggested"
    REQUIRED = "required"


class ObservationSource(StrEnum):
    """Source of observation preview captured for model-facing memory."""

    TOOL_STDOUT = "tool_stdout"
    TOOL_STDERR = "tool_stderr"
    TOOL_LOG = "tool_log"
    RUNTIME = "runtime"
    SYSTEM = "system"


class ObservationTrust(StrEnum):
    """Trust label attached to one observation preview."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNVERIFIED = "unverified"


class TrimAction(StrEnum):
    """Action taken by context trimming pipeline for one element."""

    KEPT = "kept"
    DIGESTED = "digested"
    REPLACED_WITH_ARTIFACT = "replaced_with_artifact"
    DROPPED = "dropped"


__all__ = [
    "ObservationSource",
    "ObservationTrust",
    "PlanningModeState",
    "PlanningHintLevel",
    "PlanningTodoStatus",
    "TrimAction",
]
