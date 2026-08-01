"""JSON tool-content shrink stays valid JSON (structure-preserving truncation)."""

from __future__ import annotations

import json

from agent_driver.context.tool_content_shrink import shrink_json_tool_content


def test_shrinks_long_string_leaves_and_stays_valid_json() -> None:
    payload = {
        "status": "ok",
        "count": 3,
        "body": "X" * 5000,  # the long leaf
        "nested": {"note": "Y" * 3000, "id": 42},
        "items": ["Z" * 2000, "short"],
    }
    content = json.dumps(payload, ensure_ascii=True)
    out = shrink_json_tool_content(content)
    assert out is not None
    # Still parses — structure intact.
    reparsed = json.loads(out)
    assert reparsed["status"] == "ok"  # keys + scalars preserved
    assert reparsed["count"] == 3
    assert reparsed["nested"]["id"] == 42
    assert reparsed["items"][1] == "short"  # short leaves untouched
    # Long leaves truncated with an inline marker, not cut mid-structure.
    assert reparsed["body"].endswith("chars]")
    assert len(reparsed["body"]) < 5000
    assert reparsed["nested"]["note"].endswith("chars]")
    assert reparsed["items"][0].endswith("chars]")
    assert len(out) < len(content)


def test_idempotent() -> None:
    content = json.dumps({"body": "A" * 4000}, ensure_ascii=True)
    once = shrink_json_tool_content(content)
    twice = shrink_json_tool_content(once)
    # Second pass finds nothing over the leaf budget → returns unchanged, still valid.
    assert twice == once
    assert json.loads(twice)["body"].endswith("chars]")


def test_short_json_returns_unchanged() -> None:
    content = json.dumps({"ok": True, "msg": "small"}, ensure_ascii=True)
    assert shrink_json_tool_content(content) == content  # nothing to shrink


def test_non_json_returns_none() -> None:
    assert shrink_json_tool_content("just some prose, not json") is None
    assert shrink_json_tool_content("") is None
    # A bare scalar/number is not an object/array → None (caller slices prose).
    assert shrink_json_tool_content("42") is None
    assert shrink_json_tool_content('"a string"') is None


def test_malformed_json_returns_none() -> None:
    # Already-truncated/broken JSON is not re-broken — caller handles it.
    assert shrink_json_tool_content('{"body": "AAAA') is None


def test_top_level_array() -> None:
    content = json.dumps([{"text": "Q" * 3000}, {"text": "ok"}], ensure_ascii=True)
    out = shrink_json_tool_content(content)
    assert out is not None
    arr = json.loads(out)
    assert arr[0]["text"].endswith("chars]")
    assert arr[1]["text"] == "ok"
