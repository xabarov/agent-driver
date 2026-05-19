"""Pure compaction eligibility decision helpers."""

from __future__ import annotations

from agent_driver.contracts import (
    CompactionDecision,
    CompactionMode,
    CompactionSkipReason,
    SessionMemory,
)


def decide_compaction(
    *,
    enable_compaction: bool,
    enable_session_memory_compaction: bool,
    enable_llm_compaction: bool,
    token_pressure_state: str,
    lock_active: bool,
    circuit_breaker_open: bool,
    previous_failures: int,
    failure_limit: int,
    session_memory: SessionMemory | None,
) -> CompactionDecision:
    """Compute deterministic compaction decision from runtime state."""
    metadata = {
        "token_pressure_state": token_pressure_state,
        "lock_active": lock_active,
        "previous_failures": previous_failures,
        "failure_limit": failure_limit,
        "session_memory_available": session_memory is not None,
        "partial_enabled": bool(enable_compaction),
    }
    if not enable_compaction:
        return CompactionDecision(
            eligible=False,
            mode=CompactionMode.NONE,
            skip_reason=CompactionSkipReason.DISABLED,
            metadata=metadata,
        )
    if lock_active:
        return CompactionDecision(
            eligible=False,
            mode=CompactionMode.NONE,
            skip_reason=CompactionSkipReason.LOCKED,
            metadata=metadata,
        )
    if circuit_breaker_open or previous_failures >= failure_limit:
        return CompactionDecision(
            eligible=False,
            mode=CompactionMode.NONE,
            skip_reason=CompactionSkipReason.CIRCUIT_BREAKER_OPEN,
            metadata=metadata,
        )
    if token_pressure_state not in {"compact_recommended", "blocking"}:
        return CompactionDecision(
            eligible=False,
            mode=CompactionMode.NONE,
            skip_reason=CompactionSkipReason.NOT_ELIGIBLE,
            metadata=metadata,
        )
    if enable_session_memory_compaction and session_memory is not None:
        return CompactionDecision(
            eligible=True,
            mode=CompactionMode.SESSION_MEMORY,
            metadata=metadata,
        )
    if enable_llm_compaction:
        return CompactionDecision(
            eligible=True,
            mode=CompactionMode.LLM_FULL,
            metadata=metadata,
        )
    return CompactionDecision(
        eligible=True,
        mode=CompactionMode.PARTIAL,
        metadata=metadata,
    )


__all__ = ["decide_compaction"]
