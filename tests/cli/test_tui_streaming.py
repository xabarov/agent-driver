"""Tests for markdown streaming helpers."""

from agent_driver.cli.tui.streaming import MarkdownStreamBuffer


def test_markdown_stream_buffer_splits_stable_and_tail() -> None:
    """Stable output should flush on newline boundaries."""
    buffer = MarkdownStreamBuffer()

    stable, tail = buffer.append("Hello")
    assert stable == ""
    assert tail == "Hello"

    stable, tail = buffer.append(" world\nNext")
    assert stable == "Hello world\n"
    assert tail == "Next"

    assert buffer.finalize() == "Next"
    assert buffer.tail_fragment == ""
