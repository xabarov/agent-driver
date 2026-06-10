"""OpenAI-shaped error responses for the HTTP server.

OpenAI clients (and the official SDK) expect errors as
``{"error": {"message", "type", "code"}}`` with a meaningful HTTP status, not a
bare ``500 Internal Server Error``. This module maps SDK provider exceptions and
non-completed terminal runs onto that envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_driver.sdk.errors import (
    ProviderError,
    ProviderStatusError,
    ProviderTimeoutError,
    ProviderTransportError,
)

if TYPE_CHECKING:
    from agent_driver.contracts.runtime import AgentRunOutput


def error_payload(
    message: str, *, err_type: str, code: str | None = None
) -> dict[str, Any]:
    """Build the OpenAI ``{"error": {...}}`` body."""
    return {
        "error": {"message": message, "type": err_type, "param": None, "code": code}
    }


def status_and_payload_for_exception(exc: Exception) -> tuple[int, dict[str, Any]]:
    """Map an exception raised by ``agent.run`` to (status, OpenAI error body)."""
    if isinstance(exc, ProviderStatusError):
        status = int(getattr(exc, "status_code", 502) or 502)
        return status, error_payload(
            str(exc), err_type="upstream_error", code="provider_status"
        )
    if isinstance(exc, ProviderTimeoutError):
        return 504, error_payload(str(exc), err_type="timeout", code="provider_timeout")
    if isinstance(exc, ProviderTransportError):
        return 502, error_payload(
            str(exc), err_type="upstream_unavailable", code="provider_transport"
        )
    if isinstance(exc, ProviderError):
        return 502, error_payload(
            str(exc), err_type="upstream_error", code="provider_error"
        )
    return 500, error_payload(
        f"{type(exc).__name__}: {exc}", err_type="internal_error", code="internal_error"
    )


# Non-completed terminal status -> (HTTP status, error type). A completed run is
# never an error.
_TERMINAL_HTTP = {
    "failed": (500, "run_failed"),
    "timed_out": (504, "timeout"),
    "cancelled": (499, "cancelled"),
    "paused": (500, "run_incomplete"),
}


def status_and_payload_for_output(
    output: "AgentRunOutput",
) -> tuple[int, dict[str, Any]] | None:
    """Map a non-completed terminal run to (status, error body), else ``None``.

    A ``COMPLETED`` run returns ``None`` (the caller serializes it normally).
    """
    status = getattr(output.status, "value", output.status)
    if status == "completed":
        return None
    http_status, err_type = _TERMINAL_HTTP.get(status, (500, "run_failed"))
    reason = getattr(output.terminal_reason, "value", output.terminal_reason)
    message = output.answer or (
        f"run did not complete: {reason}" if reason else f"run {status}"
    )
    return http_status, error_payload(
        message, err_type=err_type, code=str(reason or status)
    )


__all__ = [
    "error_payload",
    "status_and_payload_for_exception",
    "status_and_payload_for_output",
]
