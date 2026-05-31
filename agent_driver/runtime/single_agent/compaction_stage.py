"""Compatibility shim for context-management compaction helpers."""

from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    CompactionStageHost,
    _emit_compaction_outcome,
    _emit_compaction_started,
    _maybe_emit_circuit_breaker_warning,
    apply_compaction_if_eligible,
)

__all__ = [
    "CompactionStageHost",
    "_emit_compaction_outcome",
    "_emit_compaction_started",
    "_maybe_emit_circuit_breaker_warning",
    "apply_compaction_if_eligible",
]
