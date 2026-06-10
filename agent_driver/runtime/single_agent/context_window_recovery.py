"""Compatibility shim for context-window recovery helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from .context_management.context_window_recovery import (
    REACTIVE_COMPACTION_MAX_ATTEMPTS,
    is_context_window_error,
    reactive_compaction_count,
    record_reactive_compaction,
    should_escalate,
)

__all__ = [
    "REACTIVE_COMPACTION_MAX_ATTEMPTS",
    "is_context_window_error",
    "reactive_compaction_count",
    "record_reactive_compaction",
    "should_escalate",
]
