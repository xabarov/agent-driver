"""Tests for prompt-toolkit chat prompt helpers."""

from __future__ import annotations

import os

import pytest

from agent_driver.cli.tui import prompt as tui_prompt
from agent_driver.cli.tui.prompt import SLASH_COMMANDS, SlashCommandCompleter


def test_slash_command_completer_suggests_commands() -> None:
    """Completer should suggest slash commands by prefix."""
    if not tui_prompt._PROMPT_TOOLKIT_AVAILABLE:  # pragma: no cover - optional branch
        pytest.skip("prompt_toolkit is unavailable")
    from prompt_toolkit.document import Document

    completer = SlashCommandCompleter()
    doc = Document(text="/he", cursor_position=3)
    items = list(completer.get_completions(doc, None))
    assert items
    assert any(item.text == "/help" for item in items)


def test_slash_command_completer_suggests_at_paths(tmp_path, monkeypatch) -> None:
    if not tui_prompt._PROMPT_TOOLKIT_AVAILABLE:  # pragma: no cover - optional branch
        pytest.skip("prompt_toolkit is unavailable")
    from prompt_toolkit.document import Document

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    completer = SlashCommandCompleter()
    doc = Document(text="@do", cursor_position=3)
    items = list(completer.get_completions(doc, None))
    assert items
    assert any(item.text == "@docs/" for item in items)


def test_slash_commands_list_contains_core_commands() -> None:
    """Command table should include the expected basics."""
    assert "/help" in SLASH_COMMANDS
    assert "/exit" in SLASH_COMMANDS
    assert "/reset" in SLASH_COMMANDS
    assert "/tools" in SLASH_COMMANDS


@pytest.mark.asyncio
async def test_chat_prompt_session_prompt_async(monkeypatch) -> None:
    """Async prompt should delegate to prompt_toolkit without nested asyncio.run."""
    if not tui_prompt._PROMPT_TOOLKIT_AVAILABLE:  # pragma: no cover - optional branch
        pytest.skip("prompt_toolkit is unavailable")

    async def fake_prompt_async(*_args, **_kwargs) -> str:
        return "/exit"

    monkeypatch.setattr(
        tui_prompt.PromptSession,
        "prompt_async",
        fake_prompt_async,
    )
    session = tui_prompt.ChatPromptSession(
        provider_name="openrouter",
        model_name="model",
        session_id="session_1",
    )
    assert await session.prompt_async() == "/exit"
    session.set_usage(input_tokens=1200, output_tokens=345)
    session.set_pressure("compact")
    session.set_budget_warning("messages 30/24")
    toolbar = session._toolbar()
    assert "tokens=1.2k↑/345↓" in toolbar
    assert "ctx=compact" in toolbar
    assert "budget=messages 30/24" in toolbar
    assert session.prompt_closing_frame().startswith("╰")


def test_chat_prompt_session_frame_width_adapts(monkeypatch) -> None:
    if not tui_prompt._PROMPT_TOOLKIT_AVAILABLE:  # pragma: no cover - optional branch
        pytest.skip("prompt_toolkit is unavailable")
    monkeypatch.setattr(tui_prompt.shutil, "get_terminal_size", lambda _fallback: os.terminal_size((70, 24)))
    session = tui_prompt.ChatPromptSession(
        provider_name="openrouter",
        model_name="model",
        session_id="session_1",
    )
    frame = session.prompt_closing_frame()
    assert frame.startswith("╰")
    assert frame.endswith("╯\n")
    assert len(frame.strip()) == 70


def test_chat_prompt_session_raises_without_prompt_toolkit(monkeypatch) -> None:
    """Prompt session should fail clearly when dependency is absent."""
    monkeypatch.setattr(tui_prompt, "_PROMPT_TOOLKIT_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="prompt_toolkit"):
        _ = tui_prompt.ChatPromptSession(
            provider_name="openrouter",
            model_name="model",
            session_id="session_1",
        )
