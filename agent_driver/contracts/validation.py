"""Shared validation helpers for phase 0 contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Container, Iterable
from typing import Any

# Agent-driver reserved metadata namespace. The runtime writes identity and
# gate-decision provenance under keys with this prefix; host-supplied metadata
# may not use it. Keeping host provenance out of the reserved namespace is what
# stops model- or tool-authored output from forging or overwriting the fields
# the host relies on (U2 authorship isolation). See ``ensure_bounded_json_metadata``.
RESERVED_METADATA_PREFIX = "_ad_"

# Conservative bounds for host-supplied metadata carried through the durable run
# lifecycle. These are deliberately generous — they exist to fail closed on
# accidental blobs / deep recursion, not to police normal use.
DEFAULT_MAX_METADATA_BYTES = 16_384
DEFAULT_MAX_METADATA_DEPTH = 8
DEFAULT_MAX_METADATA_KEYS = 256


def ensure_json_serializable(value: Any, *, field_name: str) -> Any:
    """Validate JSON-serializability and return the original value."""
    try:
        json.dumps(value)
    except (
        TypeError,
        ValueError,
    ) as exc:  # pragma: no cover - branch depends on invalid inputs
        raise ValueError(f"{field_name} must be JSON-serializable") from exc
    return value


def ensure_bounded_json_metadata(
    value: Any,
    *,
    field_name: str,
    max_bytes: int = DEFAULT_MAX_METADATA_BYTES,
    max_depth: int = DEFAULT_MAX_METADATA_DEPTH,
    max_keys: int = DEFAULT_MAX_METADATA_KEYS,
    allow_reserved_keys: bool = False,
) -> Any:
    """Validate JSON-safe, bounded, reserved-key-clean metadata; return it.

    Fails closed (raises ``ValueError``) when the payload is non-JSON,
    oversized, too deeply nested, has too many keys, or — unless
    ``allow_reserved_keys`` — carries any key under
    :data:`RESERVED_METADATA_PREFIX`. Serialization is deterministic
    (``sort_keys=True``) so a caller may stably hash the returned value.
    """
    try:
        serialized = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-serializable") from exc
    if len(serialized.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"{field_name} exceeds the {max_bytes}-byte metadata limit"
        )
    total_keys = 0

    def _walk(node: Any, depth: int) -> None:
        nonlocal total_keys
        if depth > max_depth:
            raise ValueError(
                f"{field_name} exceeds the max nesting depth of {max_depth}"
            )
        if isinstance(node, dict):
            for key, sub in node.items():
                total_keys += 1
                if total_keys > max_keys:
                    raise ValueError(
                        f"{field_name} exceeds the max key count of {max_keys}"
                    )
                if (
                    not allow_reserved_keys
                    and isinstance(key, str)
                    and key.startswith(RESERVED_METADATA_PREFIX)
                ):
                    raise ValueError(
                        f"{field_name} may not use the reserved key namespace "
                        f"{RESERVED_METADATA_PREFIX!r} (key={key!r})"
                    )
                _walk(sub, depth + 1)
        elif isinstance(node, (list, tuple)):
            for sub in node:
                _walk(sub, depth + 1)

    _walk(value, 1)
    return value


# Substrings that make a metadata KEY look secret-bearing. Execution metadata is
# durable and surfaced in briefs/receipts, so a credential-looking key almost
# always means a secret leaked into a field meant for non-sensitive provenance —
# fail closed rather than persist it.
SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "access_key",
    "auth",
)


def reject_secret_like_keys(value: dict[str, Any], *, field_name: str) -> None:
    """Fail closed when any top-level metadata key looks credential-bearing."""
    for key in value:
        lowered = str(key).lower()
        if any(marker in lowered for marker in SECRET_KEY_MARKERS):
            raise ValueError(
                f"{field_name} key {key!r} looks secret-bearing; execution "
                "metadata must not carry credentials"
            )


def ensure_secret_free_bounded_metadata(
    value: dict[str, Any], *, field_name: str = "metadata"
) -> dict[str, Any]:
    """Reject secret-like keys, then validate JSON-safe, bounded metadata.

    The standard metadata validator for durable execution contracts: it composes
    :func:`reject_secret_like_keys` and :func:`ensure_bounded_json_metadata` so
    every contract enforces the same policy from one place.
    """
    reject_secret_like_keys(value, field_name=field_name)
    return ensure_bounded_json_metadata(value, field_name=field_name)


def looks_like_env_name(value: str) -> bool:
    """True when ``value`` looks like an env-var *name* (``OPENAI_API_KEY``) rather
    than a secret value: non-empty, all-uppercase, no spaces. Redaction-safe
    metadata may name the env var holding a secret, never carry the secret itself.
    """
    return bool(value) and value.upper() == value and " " not in value


def is_sensitive_key(
    key: str,
    *,
    markers: Iterable[str] = SECRET_KEY_MARKERS,
    safe_keys: Container[str] = frozenset(),
) -> bool:
    """True when a metadata KEY looks secret-bearing. ``safe_keys`` (compared
    lower-cased) lists keys that legitimately contain a marker substring but never
    a secret value (e.g. ``auth_mode``, ``max_tokens``)."""
    lower = key.lower()
    if lower in safe_keys:
        return False
    return any(marker in lower for marker in markers)


def assert_no_secret_fields(
    value: Any,
    *,
    subject: str,
    path: str = "",
    markers: Iterable[str] = SECRET_KEY_MARKERS,
    safe_keys: Container[str] = frozenset(),
    value_pattern: re.Pattern[str] | None = None,
) -> None:
    """Recursively reject metadata that carries a secret VALUE.

    A sensitive key may only *name* an env var (:func:`looks_like_env_name`); any
    other value under it fails closed. ``subject`` names the metadata in the error
    (e.g. ``"harness adapter metadata"``); ``path`` seeds the dotted field path.
    When ``value_pattern`` is given, any string value matching it is rejected as a
    secret-shaped literal even under a non-sensitive key.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if is_sensitive_key(key_text, markers=markers, safe_keys=safe_keys):
                if not (isinstance(item, str) and looks_like_env_name(item)):
                    raise ValueError(
                        f"{subject} may name secret env vars but must not contain "
                        f"secret values: {next_path}"
                    )
            assert_no_secret_fields(
                item,
                subject=subject,
                path=next_path,
                markers=markers,
                safe_keys=safe_keys,
                value_pattern=value_pattern,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_secret_fields(
                item,
                subject=subject,
                path=f"{path}[{index}]",
                markers=markers,
                safe_keys=safe_keys,
                value_pattern=value_pattern,
            )
    elif value_pattern is not None and isinstance(value, str):
        if value_pattern.search(value):
            raise ValueError(
                f"{subject} must not contain secret-shaped values: {path}"
            )


def ensure_non_negative_int(value: int | None, *, field_name: str) -> int | None:
    """Validate non-negative integer metrics."""
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def ensure_positive_int(value: int | None, *, field_name: str) -> int | None:
    """Validate strictly positive integer constraints."""
    if value is not None and value <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return value


def ensure_positive_float(value: float | None, *, field_name: str) -> float | None:
    """Validate strictly positive float constraints."""
    if value is not None and value <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return value


def ensure_non_negative_float(value: float | None, *, field_name: str) -> float | None:
    """Validate non-negative float metrics."""
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value
