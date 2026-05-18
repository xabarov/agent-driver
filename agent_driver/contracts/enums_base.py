"""Shared enum base types."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum base with stable JSON-friendly representation."""
