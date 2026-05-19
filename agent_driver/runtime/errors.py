"""Runtime-specific exceptions for runner skeleton."""

from __future__ import annotations


class RuntimeExecutionError(RuntimeError):
    """Base runtime execution failure."""


class MissingCheckpointError(RuntimeExecutionError):
    """Raised when resume is requested but checkpoint is missing."""
