"""Compaction enums for layered context summarization."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class CompactionMode(StrEnum):
    """Compaction path selected by orchestrator."""

    NONE = "none"
    SESSION_MEMORY = "session_memory"
    LLM_FULL = "llm_full"
    PARTIAL = "partial"


class CompactionSkipReason(StrEnum):
    """Reason why compaction is skipped."""

    DISABLED = "disabled"
    NOT_ELIGIBLE = "not_eligible"
    LOCKED = "locked"
    MISSING_SESSION_MEMORY = "missing_session_memory"
    PATH_NOT_IMPLEMENTED = "path_not_implemented"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


__all__ = ["CompactionMode", "CompactionSkipReason"]
