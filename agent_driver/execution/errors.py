"""Typed, bounded, redaction-safe execution failures (EPIC-01).

Categorizable WITHOUT parsing the message: switch on ``type(exc)`` or
``exc.code``. Messages are bounded so they are safe to place in traces/events;
raw remote payloads never travel in an exception message — they belong in a
support artifact. Lease/stale-generation classes are reserved for later epics.
"""

from __future__ import annotations

_MAX_MESSAGE_CHARS = 500


class ExecutionError(Exception):
    """Base class for all backend execution failures.

    ``code`` is a stable machine string; ``message`` is bounded and safe for
    traces. Subclasses set ``code``; do not raise the base directly.
    """

    code: str = "execution_error"

    def __init__(self, message: str = "") -> None:
        bounded = (message or self.code)[:_MAX_MESSAGE_CHARS]
        super().__init__(bounded)
        self.message = bounded


class UnsupportedCapabilityError(ExecutionError):
    """The backend does not support the requested operation/capability. A backend
    reports unsupported instead of silently upgrading a weaker guarantee."""

    code = "unsupported_capability"


class ExecutionTimeoutError(ExecutionError):
    """The execution exceeded its timeout. (Distinct from an acquire/queue
    timeout, reserved for later epics.)"""

    code = "execution_timeout"


class ExecutionTransportError(ExecutionError):
    """Transport was interrupted; the execution identity is known so the caller
    can look it up rather than blindly re-run a mutating operation."""

    code = "execution_transport"


class IndeterminateExecutionError(ExecutionError):
    """The dispatch/result is indeterminate: the operation may or may not have
    committed. It must not be silently retried as if it did not happen."""

    code = "indeterminate_execution"


class OutputLimitExceededError(ExecutionError):
    """Output or artifact exceeded an enforced limit before it could be bounded
    into a safe reference."""

    code = "output_limit_exceeded"


class BackendProtocolError(ExecutionError):
    """The backend returned a response that violates the execution contract
    (missing/invalid identity, malformed result)."""

    code = "backend_protocol_violation"


__all__ = [
    "ExecutionError",
    "UnsupportedCapabilityError",
    "ExecutionTimeoutError",
    "ExecutionTransportError",
    "IndeterminateExecutionError",
    "OutputLimitExceededError",
    "BackendProtocolError",
]
