"""Epic 037 phase C: observer payload sanitization (bounds + secret redaction)."""

from __future__ import annotations

from agent_driver.observability.redaction import (
    MAX_SEQUENCE,
    MAX_STRING,
    is_sensitive_hook_key,
    sanitize_observer_payload,
)


def test_sensitive_keys_masked_by_value_not_metadata() -> None:
    payload = {
        "api_key": "sk-secret",
        "authorization": "Bearer xyz",
        "openai_api_key": "sk-2",
        "token_count": 42,  # NOT masked — exact-match set, not substring
        "session_id": "s-1",  # NOT masked
        "nested": {"password": "p", "model": "deepseek"},
    }
    cleaned, info = sanitize_observer_payload(payload)
    assert cleaned["api_key"] == "<redacted>"
    assert cleaned["authorization"] == "<redacted>"
    assert cleaned["openai_api_key"] == "<redacted>"
    assert cleaned["token_count"] == 42
    assert cleaned["session_id"] == "s-1"
    assert cleaned["nested"]["password"] == "<redacted>"
    assert cleaned["nested"]["model"] == "deepseek"
    assert info.applied is True
    assert set(info.redacted_fields) == {
        "api_key",
        "authorization",
        "openai_api_key",
        "password",
    }


def test_is_sensitive_hook_key_exact_and_suffix() -> None:
    assert is_sensitive_hook_key("Authorization")
    assert is_sensitive_hook_key("x_api_key")
    assert is_sensitive_hook_key("set-cookie")
    assert not is_sensitive_hook_key("token_count")
    assert not is_sensitive_hook_key("model")


def test_long_string_truncated() -> None:
    payload = {"blob": "x" * (MAX_STRING + 500)}
    cleaned, info = sanitize_observer_payload(payload)
    assert cleaned["blob"].endswith("chars]")
    assert len(cleaned["blob"]) < MAX_STRING + 100
    assert info.applied is True and info.metadata["truncated"] is True


def test_long_sequence_truncated() -> None:
    cleaned, info = sanitize_observer_payload(list(range(MAX_SEQUENCE + 50)))
    assert cleaned[-1] == {"_truncated_items": 50}
    assert info.metadata["truncated"] is True


def test_max_depth_guard() -> None:
    node: dict = {}
    cur = node
    for _ in range(20):
        cur["child"] = {}
        cur = cur["child"]
    cleaned, info = sanitize_observer_payload(node)
    assert "<max-depth>" in repr(cleaned)
    assert info.metadata["truncated"] is True


def test_clean_payload_reports_not_applied() -> None:
    cleaned, info = sanitize_observer_payload({"model": "x", "tokens": 5})
    assert cleaned == {"model": "x", "tokens": 5}
    assert info.applied is False and info.policy is None


def test_bytes_and_unserializable_degrade() -> None:
    class _Weird:
        def __repr__(self) -> str:
            return "WEIRD"

    cleaned, _ = sanitize_observer_payload({"b": b"abc", "o": _Weird()})
    assert cleaned["b"] == "<3 bytes>"
    assert cleaned["o"] == "WEIRD"
