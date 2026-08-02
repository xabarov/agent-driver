"""Runtime-specific exceptions for runner skeleton."""

from __future__ import annotations


class RuntimeExecutionError(RuntimeError):
    """Base runtime execution failure."""


class MissingCheckpointError(RuntimeExecutionError):
    """Raised when resume is requested but checkpoint is missing."""


class ResumeConflictError(RuntimeExecutionError):
    """Raised when a resume/approval cannot be applied because the run has
    moved on: the expected checkpoint no longer matches, or the targeted
    interrupt was already consumed by a prior resume.

    A stable, explicit stale/conflict outcome (U3): the host can treat a
    duplicate approval as an idempotent no-op instead of re-driving the run or
    re-executing the tool. Subclasses ``RuntimeExecutionError`` so existing
    callers that catch the base type keep working.
    """
