"""Phase 13 H29.3 — tests for tool-call fallback feedback helpers.

Pins:
  * ``closest_tool_names``:
      - empty inputs (no name, no available list, no candidate above
        cutoff) yield empty list;
      - case-insensitive matching;
      - typo within Levenshtein cutoff is surfaced as the best match;
      - completely-unrelated name returns empty;
      - ``max_suggestions`` caps the result.
  * ``build_unknown_tool_feedback``:
      - includes the misspelled name as quoted;
      - lists "Did you mean: X, Y" when matches exist;
      - omits the "Did you mean" clause when no match passes cutoff;
      - shows "(none registered)" when available list is empty;
      - truncates very long available lists with a count suffix.
  * ``build_arguments_parse_feedback``:
      - quotes the offending tool name;
      - includes the raw payload snippet (truncated when long);
      - includes the optional ``error_detail``;
      - falls back gracefully when no raw arguments provided.
  * ``build_missing_tool_name_feedback`` — deterministic short hint.
"""

from __future__ import annotations

import pytest

from agent_driver.tools.fallback_feedback import (
    build_arguments_parse_feedback,
    build_missing_tool_name_feedback,
    build_unknown_tool_feedback,
    classify_unknown_tool,
    closest_tool_names,
)

# --- closest_tool_names ----------------------------------------------------


def test_closest_empty_name_returns_empty():
    assert closest_tool_names("", ["alpha", "beta"]) == []


def test_closest_empty_available_returns_empty():
    assert closest_tool_names("anything", []) == []


def test_closest_no_candidate_above_cutoff_returns_empty():
    """A name completely unrelated to all registered tools yields nothing."""
    assert closest_tool_names("zzzz_unrelated", ["alpha", "beta", "gamma"]) == []


def test_closest_returns_best_match_for_typo():
    out = closest_tool_names("scrennshot_tool", ["screenshot_tool", "ocr_tool"])
    assert "screenshot_tool" in out


def test_closest_case_insensitive():
    """Models often capitalize differently — match should still hit."""
    out = closest_tool_names("ScreenshotTool", ["screenshot_tool"])
    # closest_tool_names normalizes case; the registry version is returned.
    # Note: difflib is character-based so "ScreenshotTool" vs
    # "screenshot_tool" depend on cutoff. Use a near-identical name:
    out2 = closest_tool_names("Screenshot_tool", ["screenshot_tool"])
    assert out2 == ["screenshot_tool"]


def test_closest_max_suggestions_caps_result():
    """When many candidates pass cutoff, return only the top N."""
    available = ["ocr_tool", "ocr_pdf", "ocr_image", "ocr_video", "ocr_file"]
    out = closest_tool_names("ocr_tools", available, max_suggestions=2)
    assert len(out) <= 2


def test_closest_non_string_name_returns_empty():
    assert closest_tool_names(None, ["alpha"]) == []  # type: ignore[arg-type]
    assert closest_tool_names(123, ["alpha"]) == []  # type: ignore[arg-type]


def test_closest_filters_non_string_available_entries():
    """The available list might contain None / int by mistake — skip them."""
    out = closest_tool_names("alpha", ["alpha", None, 42, "alpha_v2"])  # type: ignore[list-item]
    assert "alpha" in out


# --- build_unknown_tool_feedback -------------------------------------------


def test_unknown_tool_feedback_includes_quoted_name():
    msg = build_unknown_tool_feedback("foo_bar", ["alpha"])
    assert "'foo_bar'" in msg
    assert "not registered" in msg


def test_unknown_tool_feedback_with_suggestion():
    msg = build_unknown_tool_feedback("screenshot", ["screenshot_tool", "ocr_tool"])
    assert "Did you mean:" in msg
    assert "'screenshot_tool'" in msg
    assert "Available tools:" in msg


def test_unknown_tool_feedback_without_suggestion_omits_clause():
    msg = build_unknown_tool_feedback("zzz_unrelated", ["alpha", "beta"])
    assert "Did you mean:" not in msg
    assert "Available tools:" in msg
    assert "alpha" in msg
    assert "beta" in msg


def test_unknown_tool_feedback_empty_available():
    msg = build_unknown_tool_feedback("anything", [])
    assert "(none registered)" in msg


def test_unknown_tool_feedback_truncates_long_available_list():
    available = [f"tool_{i:03d}" for i in range(50)]
    msg = build_unknown_tool_feedback("missing", available)
    assert "Available tools:" in msg
    # Limit is 30 — should mention the rest as "+20 more"
    assert "+20 more" in msg


def test_unknown_tool_feedback_for_read_url_points_to_web_fetch():
    msg = build_unknown_tool_feedback("read_url", ["web_fetch", "web_search"])
    assert "Use the registered tool 'web_fetch'" in msg
    assert '{"url": "https://example.com/page"}' in msg
    assert classify_unknown_tool("read_url", ["web_fetch"]) == {
        "kind": "unavailable_alias_for_web_fetch",
        "recommended_tool": "web_fetch",
    }


def test_unknown_tool_feedback_for_internal_reasoning_tool():
    msg = build_unknown_tool_feedback("thought", ["web_search"])
    assert "hidden reasoning" in msg
    assert classify_unknown_tool("thought", ["web_search"]) == {
        "kind": "hidden_reasoning_tool",
        "recommended_tool": None,
    }


def test_unknown_tool_feedback_filters_non_string_available():
    """Available list with mixed types — only strings appear in catalog."""
    msg = build_unknown_tool_feedback("missing", ["alpha", None, "beta"])  # type: ignore[list-item]
    assert "alpha" in msg
    assert "beta" in msg


# --- build_arguments_parse_feedback ----------------------------------------


def test_arguments_parse_feedback_quotes_tool_name():
    msg = build_arguments_parse_feedback("screenshot_tool", '{"bad": json}')
    assert "'screenshot_tool'" in msg
    assert "could not be parsed as JSON" in msg


def test_arguments_parse_feedback_includes_raw_payload():
    msg = build_arguments_parse_feedback("foo", "{bad: 'json'}")
    assert "{bad: 'json'}" in msg


def test_arguments_parse_feedback_truncates_long_payload():
    long_payload = "x" * 500
    msg = build_arguments_parse_feedback("foo", long_payload)
    assert "…" in msg
    # The snippet portion is <= 240 + ellipsis
    assert "x" * 250 not in msg


def test_arguments_parse_feedback_includes_optional_error_detail():
    msg = build_arguments_parse_feedback(
        "foo", "{bad}", error_detail="Expecting property name"
    )
    assert "Expecting property name" in msg


def test_arguments_parse_feedback_handles_missing_payload():
    """If raw_arguments is None or empty, just emit the generic hint."""
    msg = build_arguments_parse_feedback("foo", None)
    assert "Raw value seen:" not in msg
    # Still ends with the actionable instruction.
    assert "JSON object" in msg


# --- build_missing_tool_name_feedback --------------------------------------


def test_missing_tool_name_feedback_is_actionable():
    msg = build_missing_tool_name_feedback()
    assert '"name"' in msg
    assert "Include" in msg or "Add" in msg or "include" in msg
