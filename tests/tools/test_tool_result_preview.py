"""Epic 029 phase A: tool-result preview/paging primitives."""

from __future__ import annotations

from agent_driver.tools import (
    empty_result_marker,
    is_truncated,
    persisted_output_envelope,
    safe_preview,
)


def test_short_text_unchanged_no_marker() -> None:
    assert safe_preview("короткий текст", max_chars=100) == "короткий текст"
    assert not is_truncated("короткий текст", max_chars=100)


def test_preview_marks_omission_and_counts_chars() -> None:
    text = "а" * 500
    out = safe_preview(text, max_chars=100)
    assert "опущено 400 символов" in out
    head = out.split("[…опущено")[0]
    assert len(head.rstrip()) <= 100  # kept head bounded


def test_preview_cut_is_codepoint_safe_for_cyrillic() -> None:
    # Every kept char must be a whole codepoint (no mojibake). Round-trips clean.
    text = "Привет мир! " * 200
    out = safe_preview(text, max_chars=137)
    out.encode("utf-8").decode("utf-8")  # would raise on a broken slice
    assert "�" not in out


def test_preview_prefers_newline_boundary() -> None:
    text = "первая строка\nвторая строка\n" + ("х" * 300)
    out = safe_preview(text, max_chars=60)
    head = out.split("[…опущено")[0]
    # The kept head ends cleanly on a line, not mid-third-line.
    assert head.rstrip().endswith("строка")


def test_preview_ignores_useless_early_newline() -> None:
    # A newline in the first few chars must NOT collapse the preview to nothing.
    text = "x\n" + ("y" * 500)
    out = safe_preview(text, max_chars=200)
    assert out.count("y") > 100


def test_empty_marker() -> None:
    assert "без вывода" in empty_result_marker()


def test_persisted_envelope_carries_preview_and_hint() -> None:
    env = persisted_output_envelope(
        preview="первые строки…",
        reread_hint="get_meeting_fragments(meeting_id=m1, query=бюджет)",
        result_key="m1:0-5",
    )
    assert "<persisted-output key=m1:0-5>" in env
    assert "первые строки" in env
    assert "get_meeting_fragments(meeting_id=m1" in env
    assert env.strip().endswith("</persisted-output>")
