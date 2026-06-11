"""Tests for generic host-facing payload sanitization."""

from __future__ import annotations

from agent_driver.adapters import REDACTED, TRUNCATED, sanitize_projection_value


def test_sanitize_projection_value_redacts_sensitive_keys_nested() -> None:
    payload = {
        "safe": "ok",
        "nested": {
            "api_key": "secret",
            "Authorization": "Bearer abc",
            "items": [{"session_token": "token"}, {"name": "kept"}],
        },
    }

    sanitized = sanitize_projection_value(payload)

    assert sanitized["safe"] == "ok"
    assert sanitized["nested"]["api_key"] == REDACTED
    assert sanitized["nested"]["Authorization"] == REDACTED
    assert sanitized["nested"]["items"][0]["session_token"] == REDACTED
    assert sanitized["nested"]["items"][1]["name"] == "kept"


def test_sanitize_projection_value_truncates_raw_payload_keys() -> None:
    sanitized = sanitize_projection_value(
        {
            "provider_payload": {"raw": "large"},
            "request": {"headers": {"Authorization": "secret"}},
            "response": "raw",
        }
    )

    assert sanitized["provider_payload"] == TRUNCATED
    assert sanitized["request"] == TRUNCATED
    assert sanitized["response"] == TRUNCATED


def test_sanitize_projection_value_truncates_long_strings_and_caps_sequences() -> None:
    sanitized = sanitize_projection_value(
        {"text": "x" * 12, "items": list(range(8)), "tuple_items": tuple(range(8))},
        max_string_chars=5,
        max_sequence_items=3,
    )

    assert sanitized["text"] == "xxxxx…"
    assert sanitized["items"] == [0, 1, 2]
    assert sanitized["tuple_items"] == [0, 1, 2]
