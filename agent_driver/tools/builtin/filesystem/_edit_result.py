"""Shared output schema helpers for write/edit-style filesystem tools."""

from __future__ import annotations


def edit_output_schema() -> dict[str, object]:
    """Return stable schema for edit/write result payloads."""
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "operation": {"type": "string"},
            "size_bytes": {"type": "integer"},
            "replacements": {"type": "integer"},
            "preview": {
                "type": "object",
                "properties": {
                    "before": {"type": "string"},
                    "after": {"type": "string"},
                    "truncated": {"type": "boolean"},
                },
                "required": ["before", "after", "truncated"],
                "additionalProperties": False,
            },
        },
        "required": ["path", "operation", "size_bytes", "replacements", "preview"],
        "additionalProperties": True,
    }


__all__ = ["edit_output_schema"]
