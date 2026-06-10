"""Compatibility shim for context-management compaction helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

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
