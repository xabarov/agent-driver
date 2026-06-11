"""Tests for provider error classification into recovery actions."""

from __future__ import annotations

import httpx
import pytest

from agent_driver.llm.error_classifier import (
    ProviderErrorReason,
    RecoveryAction,
    classify,
)
from agent_driver.sdk.errors import (
    ProviderErrorDetails,
    ProviderStatusError,
    ProviderTimeoutError,
    ProviderTransportError,
)


def _status_error(status_code: int, message: str = "") -> ProviderStatusError:
    return ProviderStatusError(
        ProviderErrorDetails(
            provider="prov",
            status_code=status_code,
            request_id="req-1",
            message=message,
        ),
        cause=RuntimeError("boom"),
    )


@pytest.mark.parametrize(
    ("status", "message", "reason", "action"),
    [
        (401, "", ProviderErrorReason.AUTH, RecoveryAction.FAIL_FAST),
        (402, "", ProviderErrorReason.BILLING, RecoveryAction.FAIL_FAST),
        (403, "", ProviderErrorReason.AUTH, RecoveryAction.FAIL_FAST),
        (
            403,
            "blocked by content policy",
            ProviderErrorReason.CONTENT_POLICY,
            RecoveryAction.FAIL_FAST,
        ),
        (
            404,
            "model gpt-x not found",
            ProviderErrorReason.MODEL_NOT_FOUND,
            RecoveryAction.FAIL_FAST,
        ),
        (408, "", ProviderErrorReason.TIMEOUT, RecoveryAction.ROTATE_PROVIDER),
        (413, "", ProviderErrorReason.PAYLOAD_TOO_LARGE, RecoveryAction.FAIL_FAST),
        (
            429,
            "slow down",
            ProviderErrorReason.RATE_LIMIT,
            RecoveryAction.BACKOFF_RETRY,
        ),
        (
            500,
            "kaboom",
            ProviderErrorReason.SERVER_ERROR,
            RecoveryAction.ROTATE_PROVIDER,
        ),
        (503, "", ProviderErrorReason.OVERLOADED, RecoveryAction.BACKOFF_RETRY),
        (
            529,
            "overloaded",
            ProviderErrorReason.OVERLOADED,
            RecoveryAction.BACKOFF_RETRY,
        ),
    ],
)
def test_status_codes_map_to_reason_and_action(
    status: int,
    message: str,
    reason: ProviderErrorReason,
    action: RecoveryAction,
) -> None:
    """Each HTTP status maps to the documented reason and recovery action."""
    classified = classify(_status_error(status, message))
    assert classified.reason is reason
    assert classified.action is action
    assert classified.status_code == status
    assert classified.request_id == "req-1"


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (
            "This model's maximum context length is 8192 tokens",
            ProviderErrorReason.CONTEXT_OVERFLOW,
        ),
        (
            "prompt is too long for the context window",
            ProviderErrorReason.CONTEXT_OVERFLOW,
        ),
        ("request entity too large", ProviderErrorReason.PAYLOAD_TOO_LARGE),
        ("flagged by moderation", ProviderErrorReason.CONTENT_POLICY),
        ("the model deployment does not exist", ProviderErrorReason.MODEL_NOT_FOUND),
        ("invalid 'tools' field", ProviderErrorReason.FORMAT_ERROR),
    ],
)
def test_bad_request_message_heuristics(
    message: str, reason: ProviderErrorReason
) -> None:
    """A 400 is disambiguated by message into overflow/payload/policy/format."""
    classified = classify(_status_error(400, message))
    assert classified.reason is reason


def test_context_overflow_is_fatal_to_rotation_but_compresses() -> None:
    """Overflow should not rotate providers; it routes to a compress action."""
    classified = classify(_status_error(400, "maximum context length exceeded"))
    assert classified.action is RecoveryAction.COMPRESS_CONTEXT
    assert classified.is_fatal is True
    assert classified.marks_unhealthy is False


def test_timeout_and_transport_errors() -> None:
    """Typed timeout/transport errors map to their reasons."""
    timeout = classify(
        ProviderTimeoutError(
            ProviderErrorDetails(
                provider="p", status_code=None, request_id=None, message="timed out"
            ),
            cause=RuntimeError("x"),
        )
    )
    assert timeout.reason is ProviderErrorReason.TIMEOUT
    assert timeout.marks_unhealthy is True

    transport = classify(
        ProviderTransportError(
            ProviderErrorDetails(
                provider="p", status_code=None, request_id=None, message="reset"
            ),
            cause=RuntimeError("x"),
        )
    )
    assert transport.reason is ProviderErrorReason.TRANSPORT
    assert transport.action is RecoveryAction.ROTATE_PROVIDER


def test_raw_httpx_status_error_on_cause_chain() -> None:
    """A raw httpx.HTTPStatusError anywhere on the cause chain is classified."""
    request = httpx.Request("POST", "https://api.example.com/v1/chat")
    response = httpx.Response(401, request=request, text="no key")
    raw = httpx.HTTPStatusError("401", request=request, response=response)
    wrapped = RuntimeError("provider failed")
    wrapped.__cause__ = raw

    classified = classify(wrapped)
    assert classified.reason is ProviderErrorReason.AUTH
    assert classified.status_code == 401
    assert classified.provider == "api.example.com"


def test_generic_runtime_error_is_unknown_rotate() -> None:
    """An unrecognized failure preserves legacy rotate-and-fail-over behavior."""
    classified = classify(RuntimeError("something odd"))
    assert classified.reason is ProviderErrorReason.UNKNOWN
    assert classified.action is RecoveryAction.ROTATE_PROVIDER
    assert classified.marks_unhealthy is True
    assert classified.is_fatal is False
