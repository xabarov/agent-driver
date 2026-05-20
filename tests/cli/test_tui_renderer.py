"""Tests for chat TUI renderers."""

from agent_driver.cli.tui.renderer import PlainRenderer, build_renderer


def test_plain_renderer_outputs_legacy_prefixes() -> None:
    """Plain renderer should keep line-compatible chat output."""
    output: list[str] = []
    renderer = PlainRenderer(output=output.append)

    renderer.welcome(
        provider_name="openrouter",
        model_name="test-model",
        session_id="session_1",
        thread_id="thread_1",
        tools_count=3,
    )
    renderer.emit_tool("tool start web_search")
    renderer.emit_tool_card(
        name="web_search",
        args_summary='query="moscow weather"',
        status="completed",
        result_summary="5 results",
    )
    renderer.emit_warning("warning kind=sample")
    renderer.emit_event("run run_completed")
    renderer.emit_run_summary("run_1", 1, 1, duration_seconds=2.4)

    text = "".join(output)
    assert "chat> session=session_1 thread=thread_1" in text
    assert "tool> tool start web_search" in text
    assert "tool> web_search(query=\"moscow weather\") status=completed summary=5 results" in text
    assert "warn> warning kind=sample" in text
    assert "event> run run_completed" in text
    assert "run> run_1 tools_used=1 warnings=1" in text


def test_build_renderer_plain_mode() -> None:
    """Factory should create plain renderer for plain mode."""
    renderer = build_renderer(output=lambda _text: None, ui_mode="plain")
    assert renderer.rich_enabled is False


def test_rich_renderer_welcome_emits_panel_once() -> None:
    """Rich welcome should not duplicate output via record+export."""
    from agent_driver.cli.tui.renderer import RichRenderer

    output: list[str] = []
    renderer = RichRenderer(output=output.append)
    if not renderer.rich_enabled:  # pragma: no cover - optional dependency
        return
    renderer.welcome(
        provider_name="openrouter",
        model_name="test-model",
        session_id="session_1",
        thread_id="thread_1",
        tools_count=2,
        python_backend="local",
        cwd="/tmp/proj",
        git_branch="main",
        mode_label="chat",
    )
    text = "".join(output)
    assert text.count("Welcome to agent-driver") == 1
    assert text.count("╭─ chat") == 1
    assert "! for bash" in text
    assert "cwd:" in text
    assert "mode:" in text
    assert "branch:" in text
    assert "python:" in text
    assert "/reset clears memory" in text
    assert "Type /help for commands" not in text


def test_rich_renderer_tool_card_format() -> None:
    from agent_driver.cli.tui.renderer import RichRenderer

    output: list[str] = []
    renderer = RichRenderer(output=output.append)
    if not renderer.rich_enabled:  # pragma: no cover
        return
    renderer.emit_tool_card(
        name="web_search",
        args_summary='query="moscow weather"',
        status="completed",
        result_summary="5 results via duckduckgo",
    )
    text = "".join(output)
    assert "web_search" in text
    assert "moscow" in text
    assert "⎿" in text


def test_rich_renderer_assistant_prefix_and_tail() -> None:
    from agent_driver.cli.tui.renderer import RichRenderer

    output: list[str] = []
    renderer = RichRenderer(output=output.append)
    if not renderer.rich_enabled:  # pragma: no cover
        return
    renderer.emit_raw(renderer.assistant_prefix())
    renderer.emit_assistant_tail("Hello")
    text = "".join(output)
    assert "\n●\n" in text
    assert "Hello" in text


def test_rich_renderer_denied_tool_card_highlights_reason() -> None:
    from agent_driver.cli.tui.renderer import RichRenderer

    output: list[str] = []
    renderer = RichRenderer(output=output.append)
    if not renderer.rich_enabled:  # pragma: no cover
        return
    renderer.emit_tool_card(
        name="todo_write",
        args_summary="items=1",
        status="denied",
        result_summary="todo.id is required",
    )
    text = "".join(output)
    assert "status=denied" in text
    assert "reason:" in text
    assert "todo.id is required" in text


def test_rich_renderer_tool_card_shows_truncated_badge() -> None:
    from agent_driver.cli.tui.renderer import RichRenderer

    output: list[str] = []
    renderer = RichRenderer(output=output.append)
    if not renderer.rich_enabled:  # pragma: no cover
        return
    renderer.emit_tool_card(
        name="glob_search",
        args_summary="pattern=**/*",
        status="completed",
        result_summary="200 paths matched",
        truncated=True,
    )
    text = "".join(output)
    assert "truncated" in text
