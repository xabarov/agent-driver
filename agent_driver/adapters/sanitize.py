"""Generic sanitizers for host-facing projection payloads."""

from __future__ import annotations

from typing import Any

DEFAULT_MAX_STRING_CHARS = 2000
DEFAULT_MAX_SEQUENCE_ITEMS = 50

REDACTED = "[redacted]"
TRUNCATED = "[truncated]"

DEFAULT_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "session_token",
    "token",
)

DEFAULT_RAW_PAYLOAD_KEY_PARTS = (
    "debug_payload",
    "provider_payload",
    "raw_payload",
    "raw_request",
    "raw_response",
)
DEFAULT_RAW_PAYLOAD_KEYS = frozenset({"request", "response"})


def should_redact_key(
    key: str, *, sensitive_key_parts: tuple[str, ...] = DEFAULT_SENSITIVE_KEY_PARTS
) -> bool:
    """Return True when a field name looks like it may contain credentials."""
    normalized = key.lower()
    return any(part in normalized for part in sensitive_key_parts)


def should_truncate_raw_payload_key(
    key: str,
    *,
    raw_payload_keys: frozenset[str] = DEFAULT_RAW_PAYLOAD_KEYS,
    raw_payload_key_parts: tuple[str, ...] = DEFAULT_RAW_PAYLOAD_KEY_PARTS,
) -> bool:
    """Return True for bulky provider/debug payload fields."""
    normalized = key.lower()
    return normalized in raw_payload_keys or any(
        part in normalized for part in raw_payload_key_parts
    )


def sanitize_projection_value(
    value: Any,
    *,
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
    max_sequence_items: int = DEFAULT_MAX_SEQUENCE_ITEMS,
) -> Any:
    """Redact secrets and cap bulky payloads before host UI projection.

    This helper is intentionally domain-neutral: hosts can apply it to
    warning, SSE, CLI, or support-bundle payloads before exposing data to
    operators.
    """
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if should_redact_key(key):
                sanitized[key] = REDACTED
                continue
            if should_truncate_raw_payload_key(key):
                sanitized[key] = TRUNCATED
                continue
            sanitized[key] = sanitize_projection_value(
                raw_value,
                max_string_chars=max_string_chars,
                max_sequence_items=max_sequence_items,
            )
        return sanitized
    if isinstance(value, list):
        return [
            sanitize_projection_value(
                item,
                max_string_chars=max_string_chars,
                max_sequence_items=max_sequence_items,
            )
            for item in value[:max_sequence_items]
        ]
    if isinstance(value, tuple):
        return [
            sanitize_projection_value(
                item,
                max_string_chars=max_string_chars,
                max_sequence_items=max_sequence_items,
            )
            for item in value[:max_sequence_items]
        ]
    if isinstance(value, str) and len(value) > max_string_chars:
        return value[:max_string_chars] + "…"
    return value


__all__ = [
    "DEFAULT_MAX_SEQUENCE_ITEMS",
    "DEFAULT_MAX_STRING_CHARS",
    "DEFAULT_RAW_PAYLOAD_KEY_PARTS",
    "DEFAULT_RAW_PAYLOAD_KEYS",
    "DEFAULT_SENSITIVE_KEY_PARTS",
    "REDACTED",
    "TRUNCATED",
    "sanitize_projection_value",
    "should_redact_key",
    "should_truncate_raw_payload_key",
]
