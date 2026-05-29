"""Compaction sanitizer and PTL retry tests."""

from __future__ import annotations

from agent_driver.context.compaction import (
    ptl_retry_drop_oldest_groups,
    sanitize_compaction_text,
)


def test_sanitizer_redacts_secret_like_tokens() -> None:
    """Secret-like strings should be redacted in compaction input."""
    text = "api_key=supersecret123 and sk-ABCDEFGH1234 and attachment://file.png"
    cleaned = sanitize_compaction_text(text)
    assert "[REDACTED_SECRET]" in cleaned
    assert "attachment://" not in cleaned


def test_ptl_retry_drops_oldest_groups_under_budget() -> None:
    """PTL retry should drop oldest groups first while preserving order."""
    groups = ["aaaa", "bbbb", "cccc", "dddd"]
    kept, dropped = ptl_retry_drop_oldest_groups(groups=groups, max_chars=10)
    assert dropped == ["aaaa", "bbbb"]
    assert kept == ["cccc", "dddd"]
