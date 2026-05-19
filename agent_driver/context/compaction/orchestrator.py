"""Compaction orchestrator helpers for Phase 8."""

from __future__ import annotations

from dataclasses import dataclass, field
from agent_driver.context.compaction.eligibility import decide_compaction
from agent_driver.contracts import (
    CompactionAudit,
    CompactionDecision,
    CompactionMode,
    CompactionResult,
    SessionMemory,
)


@dataclass(slots=True)
class CompactionOrchestrator:
    """Coordinates eligibility state, lock, and failure counters."""

    failure_limit: int = 3
    _lock_active: bool = False
    _consecutive_failures: int = 0
    _last_compaction_id: int = 0

    def decide(
        self,
        *,
        enable_compaction: bool,
        enable_session_memory_compaction: bool,
        enable_llm_compaction: bool,
        token_pressure_state: str,
        session_memory: SessionMemory | None,
    ) -> CompactionDecision:
        """Return pure decision based on current runtime state."""
        return decide_compaction(
            enable_compaction=enable_compaction,
            enable_session_memory_compaction=enable_session_memory_compaction,
            enable_llm_compaction=enable_llm_compaction,
            token_pressure_state=token_pressure_state,
            lock_active=self._lock_active,
            circuit_breaker_open=self._consecutive_failures >= self.failure_limit,
            previous_failures=self._consecutive_failures,
            failure_limit=self.failure_limit,
            session_memory=session_memory,
        )

    def start_attempt(self) -> str:
        """Activate lock and issue deterministic compaction id."""
        if self._lock_active:
            raise RuntimeError("compaction lock already active")
        self._lock_active = True
        self._last_compaction_id += 1
        return f"cmp_{self._last_compaction_id}"

    def complete_attempt(
        self,
        *,
        decision: CompactionDecision,
        result: CompactionResult | None = None,
        failures: list[dict[str, str]] | None = None,
    ) -> CompactionAudit:
        """Finalize one attempt, update counters, and release lock."""
        normalized_failures = list(failures or [])
        has_failure = bool(normalized_failures) or (
            result is not None and result.success is False
        )
        if has_failure:
            self._consecutive_failures += 1
        elif result is not None and result.success:
            self._consecutive_failures = 0
        self._lock_active = False
        return CompactionAudit(
            decision=decision,
            result=result,
            failures=normalized_failures,
        )

    def reset_failures(self) -> None:
        """Reset consecutive failure counter after successful compaction."""
        self._consecutive_failures = 0


@dataclass(slots=True)
class SessionMemoryFreshness:
    """Deterministic freshness classification for session memory."""

    state: str
    reason: str
    coverage_ratio: float


def evaluate_session_memory_freshness(
    *,
    session_memory: SessionMemory | None,
    latest_turn_index: int,
    stale_after_turns: int = 4,
) -> SessionMemoryFreshness:
    """Classify session memory as missing/stale/fresh."""
    if session_memory is None:
        return SessionMemoryFreshness(
            state="missing", reason="session_memory_missing", coverage_ratio=0.0
        )
    delta = max(0, latest_turn_index - session_memory.last_summarized_turn_index)
    coverage_ratio = (
        session_memory.last_summarized_turn_index / latest_turn_index
        if latest_turn_index > 0
        else 1.0
    )
    if delta > stale_after_turns:
        return SessionMemoryFreshness(
            state="stale",
            reason="summary_turn_gap_exceeded",
            coverage_ratio=round(coverage_ratio, 4),
        )
    return SessionMemoryFreshness(
        state="fresh",
        reason="within_turn_gap_budget",
        coverage_ratio=round(coverage_ratio, 4),
    )


@dataclass(slots=True)
class SessionMemoryCompactionOutput:
    """Compacted context built from session memory plus bounded tail."""

    prompt_messages: list[dict[str, str]]
    retained_digest_ids: list[str] = field(default_factory=list)
    retained_artifact_ids: list[str] = field(default_factory=list)
    retained_observation_ids: list[str] = field(default_factory=list)


def build_session_memory_compaction(
    *,
    session_memory: SessionMemory,
    recent_tail_messages: list[dict[str, str]],
    planning_state: dict[str, object] | None,
    retained_digest_ids: list[str],
    retained_artifact_ids: list[str],
    recent_tail_limit: int = 6,
) -> SessionMemoryCompactionOutput:
    """Build deterministic compacted context without LLM call."""
    bounded_tail = recent_tail_messages[-recent_tail_limit:]
    memory_message = {
        "role": "system",
        "content": (
            "Session memory summary:\n"
            f"{session_memory.summary}\n\n"
            f"Key facts: {', '.join(session_memory.key_facts) if session_memory.key_facts else '-'}\n"
            f"Pending tasks: {', '.join(session_memory.pending_tasks) if session_memory.pending_tasks else '-'}"
        ),
    }
    planning_message = None
    if planning_state:
        planning_message = {
            "role": "system",
            "content": f"Planning state snapshot: {planning_state}",
        }
    messages = [memory_message]
    if planning_message is not None:
        messages.append(planning_message)
    messages.extend(
        [
            {"role": str(item.get("role", "user")), "content": str(item.get("content", ""))}
            for item in bounded_tail
        ]
    )
    return SessionMemoryCompactionOutput(
        prompt_messages=messages,
        retained_digest_ids=list(dict.fromkeys(retained_digest_ids)),
        retained_artifact_ids=list(dict.fromkeys(retained_artifact_ids)),
        retained_observation_ids=[],
    )


__all__ = [
    "CompactionOrchestrator",
    "SessionMemoryCompactionOutput",
    "SessionMemoryFreshness",
    "build_session_memory_compaction",
    "evaluate_session_memory_freshness",
]
