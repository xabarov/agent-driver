"""Phase 11 H14 — tests for context-window error detection + reactive
compaction accounting.

Pins:
* detector recognizes the major provider error shapes (OpenAI,
  Anthropic, Gemini, generic);
* detector rejects unrelated 4xx/5xx errors (auth, rate-limit,
  transport);
* attempt counter increments per ``record_reactive_compaction`` call;
* circuit breaker fires at ``REACTIVE_COMPACTION_MAX_ATTEMPTS``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from agent_driver.runtime.single_agent.context_window_recovery import (
    REACTIVE_COMPACTION_MAX_ATTEMPTS,
    is_context_window_error,
    reactive_compaction_count,
    record_reactive_compaction,
    should_escalate,
)


def _http_error(status: int, body: dict | str) -> httpx.HTTPStatusError:
    """Build a fake httpx.HTTPStatusError with controlled response body."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    if isinstance(body, dict):
        response.text = json.dumps(body)
        response.json = lambda: body
    else:
        response.text = body
        response.json = MagicMock(side_effect=ValueError("not json"))
    return httpx.HTTPStatusError(
        message=f"http {status}",
        request=MagicMock(spec=httpx.Request),
        response=response,
    )


# -- detector positives ------------------------------------------------------


def test_detects_openai_context_length_exceeded():
    exc = _http_error(
        400,
        {
            "error": {
                "code": "context_length_exceeded",
                "message": "This model's maximum context length is 128000 tokens.",
                "type": "invalid_request_error",
            }
        },
    )
    assert is_context_window_error(exc) is True


def test_detects_anthropic_input_is_too_long():
    exc = _http_error(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "message": "input is too long: 215000 tokens (limit 200000).",
            }
        },
    )
    assert is_context_window_error(exc) is True


def test_detects_gemini_maximum_context_length():
    exc = _http_error(
        400,
        {
            "error": {
                "code": "invalid_argument",
                "message": "The request exceeds maximum context length for this model.",
            }
        },
    )
    assert is_context_window_error(exc) is True


def test_detects_generic_token_limit_text():
    exc = _http_error(400, "Token limit exceeded for this conversation")
    assert is_context_window_error(exc) is True


def test_detects_runtime_error_with_relevant_message():
    """Some providers raise non-HTTP errors for stream-side context overflow."""
    exc = RuntimeError("Stream broken: prompt is too long for model window")
    assert is_context_window_error(exc) is True


def test_case_insensitive_match():
    exc = _http_error(400, "CONTEXT_LENGTH_EXCEEDED")
    assert is_context_window_error(exc) is True


# -- detector negatives ------------------------------------------------------


def test_rejects_auth_error():
    exc = _http_error(
        401,
        {"error": {"code": "invalid_api_key", "message": "Bad token"}},
    )
    assert is_context_window_error(exc) is False


def test_rejects_rate_limit():
    exc = _http_error(
        429,
        {"error": {"code": "rate_limit_exceeded", "message": "Slow down"}},
    )
    assert is_context_window_error(exc) is False


def test_rejects_server_error():
    exc = _http_error(500, "Internal server error")
    assert is_context_window_error(exc) is False


def test_rejects_unrelated_runtime_error():
    exc = RuntimeError("Stream cancelled by user")
    assert is_context_window_error(exc) is False


# -- accounting --------------------------------------------------------------


def test_count_starts_at_zero():
    metadata: dict = {}
    assert reactive_compaction_count(metadata) == 0
    assert should_escalate(metadata) is False


def test_record_increments_count():
    metadata: dict = {}
    n = record_reactive_compaction(metadata, outcome="attempted")
    assert n == 1
    assert reactive_compaction_count(metadata) == 1
    assert should_escalate(metadata) is False


def test_circuit_breaker_fires_at_max():
    metadata: dict = {}
    for i in range(REACTIVE_COMPACTION_MAX_ATTEMPTS):
        record_reactive_compaction(metadata, outcome="attempted")
        assert reactive_compaction_count(metadata) == i + 1
    # Now at cap → should_escalate True.
    assert should_escalate(metadata) is True


def test_record_with_reason_includes_reason_in_metadata():
    metadata: dict = {}
    record_reactive_compaction(
        metadata, outcome="attempted", reason="context_length_exceeded"
    )
    entries = metadata["reactive_compactions"]
    assert entries[0]["outcome"] == "attempted"
    assert entries[0]["reason"] == "context_length_exceeded"


def test_count_ignores_malformed_metadata():
    """Stale or malformed metadata shape → count returns 0 safely."""
    assert reactive_compaction_count({"reactive_compactions": "not a list"}) == 0
    assert reactive_compaction_count({"reactive_compactions": [1, "bad"]}) == 0
    assert (
        reactive_compaction_count(
            {"reactive_compactions": [{"outcome": "a"}, {"outcome": "b"}]}
        )
        == 2
    )


def test_record_initializes_list_when_missing():
    metadata: dict = {"other_key": "preserved"}
    record_reactive_compaction(metadata, outcome="first")
    assert metadata["other_key"] == "preserved"
    assert isinstance(metadata["reactive_compactions"], list)
    assert len(metadata["reactive_compactions"]) == 1


def test_record_preserves_existing_attempts():
    metadata: dict = {
        "reactive_compactions": [{"outcome": "earlier", "reason": "x"}]
    }
    record_reactive_compaction(metadata, outcome="later")
    assert len(metadata["reactive_compactions"]) == 2
    assert metadata["reactive_compactions"][0]["outcome"] == "earlier"
    assert metadata["reactive_compactions"][1]["outcome"] == "later"
