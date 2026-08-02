"""U2 (epic 050) — bounded, reserved-key-clean metadata validator."""

from __future__ import annotations

import pytest

from agent_driver.contracts.validation import (
    RESERVED_METADATA_PREFIX,
    ensure_bounded_json_metadata,
)


def test_accepts_normal_metadata() -> None:
    value = {"engagement": "opaque", "nested": {"k": [1, 2, 3]}}
    assert ensure_bounded_json_metadata(value, field_name="m") is value


def test_rejects_reserved_namespace_by_default() -> None:
    with pytest.raises(ValueError, match="reserved key namespace"):
        ensure_bounded_json_metadata(
            {f"{RESERVED_METADATA_PREFIX}x": 1}, field_name="m"
        )


def test_rejects_reserved_namespace_nested() -> None:
    with pytest.raises(ValueError, match="reserved key namespace"):
        ensure_bounded_json_metadata(
            {"outer": {f"{RESERVED_METADATA_PREFIX}x": 1}}, field_name="m"
        )


def test_allows_reserved_namespace_when_opted_in() -> None:
    value = {f"{RESERVED_METADATA_PREFIX}gate_provenance": {"decision_id": "d"}}
    assert (
        ensure_bounded_json_metadata(
            value, field_name="m", allow_reserved_keys=True
        )
        is value
    )


def test_rejects_oversized() -> None:
    with pytest.raises(ValueError, match="byte"):
        ensure_bounded_json_metadata(
            {"blob": "x" * 20_000}, field_name="m", max_bytes=1024
        )


def test_rejects_too_deep() -> None:
    node: dict = {"v": 1}
    for _ in range(12):
        node = {"n": node}
    with pytest.raises(ValueError, match="nesting depth"):
        ensure_bounded_json_metadata(node, field_name="m", max_depth=8)


def test_rejects_too_many_keys() -> None:
    with pytest.raises(ValueError, match="key count"):
        ensure_bounded_json_metadata(
            {str(i): i for i in range(50)}, field_name="m", max_keys=10
        )


def test_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="JSON-serializable"):
        ensure_bounded_json_metadata({"bad": object()}, field_name="m")
