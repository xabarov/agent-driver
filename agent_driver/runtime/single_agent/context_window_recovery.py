"""Compatibility shim for context-window recovery helpers."""

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
