"""Epic 016+: body/throttle 400s must not classify as overflow (destructive compress)."""

from __future__ import annotations

import json

import httpx

from agent_driver.llm.error_classifier import (
    ProviderErrorReason,
    RecoveryAction,
    classify,
)


def _http_error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/messages")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_invalid_body_400_is_format_error_not_overflow() -> None:
    err = classify(_http_error(400, "messages: text content blocks must be non-empty"))
    assert err.reason is ProviderErrorReason.FORMAT_ERROR
    # The key invariant: a body error must NOT trigger destructive compression.
    assert err.action is not RecoveryAction.COMPRESS_CONTEXT


def test_throttling_400_is_rate_limit_not_overflow() -> None:
    # "throttling" must beat overflow's "too many tokens" and back off, not compress.
    err = classify(_http_error(400, "Throttling: too many tokens per minute for this account"))
    assert err.reason is ProviderErrorReason.RATE_LIMIT
    assert err.action is RecoveryAction.BACKOFF_RETRY


def test_genuine_overflow_400_still_compresses() -> None:
    err = classify(_http_error(400, "prompt is too long for the context window"))
    assert err.reason is ProviderErrorReason.CONTEXT_OVERFLOW
    assert err.action is RecoveryAction.COMPRESS_CONTEXT


def test_bedrock_envelope_message_is_extracted() -> None:
    body = json.dumps(
        {
            "errorMessage": "input is too long: maximum allowed input length exceeded",
            "errorCode": "ValidationException",
            "errorArgs": {"reason": "context window"},
        }
    )
    err = classify(_http_error(400, body))
    assert err.reason is ProviderErrorReason.CONTEXT_OVERFLOW


def test_invalid_request_body_marker() -> None:
    err = classify(_http_error(400, '{"error": "invalid_request_body"}'))
    assert err.reason is ProviderErrorReason.FORMAT_ERROR
