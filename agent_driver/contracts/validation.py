"""Shared validation helpers for phase 0 contracts."""

from __future__ import annotations

import json
from typing import Any


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
