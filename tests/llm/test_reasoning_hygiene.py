"""Epic 043 A: inline CoT must never survive into persisted assistant text."""

from __future__ import annotations

import pytest

from agent_driver.llm.reasoning_hygiene import strip_leading_think_block


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<think>plan the answer</think>The answer.", "The answer."),
        ("  \n<think>x</think>\n\nAnswer body.", "Answer body."),
        ("<THINK>case-insensitive</THINK>ok", "ok"),
        ("<thinking>long variant</thinking>ok", "ok"),
        (
            "<think>first</think><think>second</think>final",
            "final",
        ),
    ],
)
def test_strips_leading_think_blocks(text: str, expected: str) -> None:
    cleaned, stripped = strip_leading_think_block(text)
    assert cleaned == expected
    assert stripped == len(text) - len(expected)


def test_unterminated_leading_block_is_all_reasoning() -> None:
    text = "<think>model never closed the tag and rambled on"
    cleaned, stripped = strip_leading_think_block(text)
    assert cleaned == ""
    assert stripped == len(text)


@pytest.mark.parametrize(
    "text",
    [
        "Plain answer without tags.",
        "Answer that quotes a `<think>` tag mid-text.</think>",
        "```\n<think>inside a code fence, not leading</think>\n```",
        "",
    ],
)
def test_non_leading_or_absent_blocks_untouched(text: str) -> None:
    cleaned, stripped = strip_leading_think_block(text)
    assert cleaned == text
    assert stripped == 0


def test_multiline_reasoning_body_is_removed() -> None:
    text = "<think>\nstep 1\nstep 2\n</think>\nFinal.\n<think>quoted later</think>"
    cleaned, stripped = strip_leading_think_block(text)
    assert cleaned == "Final.\n<think>quoted later</think>"
    assert stripped > 0
