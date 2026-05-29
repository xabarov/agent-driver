"""Subagent execution and fanout lifecycle enums."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class SubagentTerminalState(StrEnum):
    """Terminal state of child/subagent execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    KILLED = "killed"
    TIMED_OUT = "timed_out"


class SubagentExecutionMode(StrEnum):
    """Execution mode for child agents."""

    SYNC = "sync"
    BACKGROUND = "background"


class SubagentStatus(StrEnum):
    """Normalized subagent task status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ParentStateWriteMode(StrEnum):
    """How child output is merged into parent state."""

    BOUNDED_APPEND_ONLY = "bounded_append_only"
    REPLACE = "replace"
    NONE = "none"


class SubagentJoinPolicy(StrEnum):
    """Join policy for fan-out child run groups."""

    WAIT_ALL = "wait_all"
    WAIT_ANY = "wait_any"
    K_OF_N = "k_of_n"
    BEST_EFFORT_UNTIL_DEADLINE = "best_effort_until_deadline"
    RACE = "race"
    MANUAL_REVIEW = "manual_review"


class SubagentMergeMode(StrEnum):
    """Merge mode for child outputs in one group."""

    APPEND = "append"
    RANK = "rank"
    SYNTHESIZE = "synthesize"
    VOTE = "vote"
    MANUAL = "manual"


class SubagentGroupStatus(StrEnum):
    """Lifecycle status of one subagent fan-out group."""

    PENDING = "pending"
    RUNNING = "running"
    JOINED = "joined"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


__all__ = [
    "ParentStateWriteMode",
    "SubagentExecutionMode",
    "SubagentGroupStatus",
    "SubagentJoinPolicy",
    "SubagentMergeMode",
    "SubagentStatus",
    "SubagentTerminalState",
]
