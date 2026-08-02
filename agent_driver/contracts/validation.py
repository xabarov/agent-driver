"""Shared validation helpers for phase 0 contracts."""

from __future__ import annotations

import json
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
