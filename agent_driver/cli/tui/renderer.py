"""Rich/plain renderers for chat stream events."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
import shutil
import sys
from typing import Protocol

from agent_driver.cli.tui.glyphs import BLACK_CIRCLE, BRANCH, DOT
from agent_driver.cli.tui.theme import DEFAULT_THEME, ChatTheme

try:  # pragma: no cover - optional dependency
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    Console = object  # type: ignore[assignment,misc]
    Markdown = object  # type: ignore[assignment,misc]
    Panel = object  # type: ignore[assignment,misc]
    _RICH_AVAILABLE = False


class ChatRenderer(Protocol):
    """Renderer protocol for chat UI."""

    @property
    def rich_enabled(self) -> bool: ...

    @property
    def live_console(self) -> object | None: ...

    def welcome(
        self,
        *,
        provider_name: str,
        model_name: str | None,
        session_id: str,
        thread_id: str,
        tools_count: int,
        python_backend: str | None = None,
        cwd: str | None = None,
        git_branch: str | None = None,
        mode_label: str | None = None,
    ) -> None: ...

    def assistant_prefix(self) -> str: ...

    def emit_assistant_delta(self, delta: str) -> None: ...

    def emit_assistant_tail(self, text: str) -> None: ...

    def emit_tool(self, compact: str) -> None: ...

    def emit_tool_card(
        self,
        *,
        name: str,
        args_summary: str,
        status: str | None,
        result_summary: str | None,
        truncated: bool | None = None,
        error_code: str | None = None,
    ) -> None: ...

    def emit_warning(self, compact: str) -> None: ...

    def emit_event(self, compact: str) -> None: ...

    def emit_run_summary(
        self, run_id: str, tools_used: int, warnings_seen: int, duration_seconds: float | None = None
    ) -> None: ...

    def emit_help(self, text: str) -> None: ...

    def emit_error_card(self, *, title: str, reason: str, hint: str | None = None) -> None: ...

    def emit_raw(self, text: str) -> None: ...


@dataclass(slots=True)
class PlainRenderer:
    """Deterministic line-based renderer used in tests and --plain mode."""

    output: Callable[[str], None]

    @property
    def rich_enabled(self) -> bool:
        return False

    @property
    def live_console(self) -> object | None:
        return None

    def welcome(
        self,
        *,
        provider_name: str,
        model_name: str | None,
        session_id: str,
        thread_id: str,
        tools_count: int,
        python_backend: str | None = None,
        cwd: str | None = None,
        git_branch: str | None = None,
        mode_label: str | None = None,
    ) -> None:
        _ = (
            provider_name,
            model_name,
            tools_count,
            python_backend,
            cwd,
            git_branch,
            mode_label,
        )
        self.output(f"chat> session={session_id} thread={thread_id}\n")
        self.output("chat> type /help for commands\n")

    def assistant_prefix(self) -> str:
        return "assistant> "

    def emit_assistant_delta(self, delta: str) -> None:
        self.output(delta)

    def emit_assistant_tail(self, text: str) -> None:
        if text:
            self.output(text)

    def emit_tool(self, compact: str) -> None:
        self.output(f"tool> {compact}\n")

    def emit_tool_card(
        self,
        *,
        name: str,
        args_summary: str,
        status: str | None,
        result_summary: str | None,
        truncated: bool | None = None,
        error_code: str | None = None,
    ) -> None:
        _ = error_code
        suffix = f" status={status}" if status else ""
        if truncated:
            suffix += " truncated=true"
        summary = result_summary or "ok"
        self.output(f"tool> {name}({args_summary}){suffix} summary={summary}\n")

    def emit_warning(self, compact: str) -> None:
        self.output(f"warn> {compact}\n")

    def emit_event(self, compact: str) -> None:
        self.output(f"event> {compact}\n")

    def emit_run_summary(
        self, run_id: str, tools_used: int, warnings_seen: int, duration_seconds: float | None = None
    ) -> None:
        _ = duration_seconds
        self.output(f"run> {run_id} tools_used={tools_used} warnings={warnings_seen}\n")

    def emit_help(self, text: str) -> None:
        self.output(text)

    def emit_error_card(self, *, title: str, reason: str, hint: str | None = None) -> None:
        self.output(f"error> {title}: {reason}\n")
        if hint:
            self.output(f"hint> {hint}\n")

    def emit_raw(self, text: str) -> None:
        self.output(text)


@dataclass(slots=True)
class RichRenderer:
    """Pretty renderer for interactive chat sessions."""

    output: Callable[[str], None]
    theme: ChatTheme = DEFAULT_THEME
    _fallback: PlainRenderer = field(init=False)
    _live_console: Console | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._fallback = PlainRenderer(self.output)
        if _RICH_AVAILABLE:
            self._live_console = Console(
                file=sys.stdout,
                force_terminal=True,
                color_system="standard",
                soft_wrap=True,
            )

    @property
    def rich_enabled(self) -> bool:
        return _RICH_AVAILABLE

    @property
    def live_console(self) -> object | None:
        return self._live_console

    def welcome(
        self,
        *,
        provider_name: str,
        model_name: str | None,
        session_id: str,
        thread_id: str,
        tools_count: int,
        python_backend: str | None = None,
        cwd: str | None = None,
        git_branch: str | None = None,
        mode_label: str | None = None,
    ) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.welcome(
                provider_name=provider_name,
                model_name=model_name,
                session_id=session_id,
                thread_id=thread_id,
                tools_count=tools_count,
                python_backend=python_backend,
                cwd=cwd,
                git_branch=git_branch,
                mode_label=mode_label,
            )
            return
        terminal_width = shutil.get_terminal_size((120, 24)).columns
        show_banner = terminal_width >= 60
        context_row = f"[{self.theme.subtle}]cwd:[/] {cwd or str(Path.cwd())}"
        if git_branch:
            context_row += f"  [{self.theme.subtle}]branch:[/] {git_branch}"
        if mode_label:
            context_row += f"  [{self.theme.subtle}]mode:[/] {mode_label}"
        body = (
            f"[{self.theme.brand}]Welcome to agent-driver[/]\n"
            f"[{self.theme.subtle}]provider:[/] {provider_name}  "
            f"[{self.theme.subtle}]model:[/] {model_name or 'default'}\n"
            f"[{self.theme.subtle}]session:[/] {session_id}  "
            f"[{self.theme.subtle}]thread:[/] {thread_id}  "
            f"[{self.theme.subtle}]tools:[/] {tools_count}"
            + (
                f"  [{self.theme.subtle}]python:[/] {python_backend}"
                if isinstance(python_backend, str) and python_backend.strip()
                else ""
            )
            + "\n"
            f"{context_row}"
        )
        if show_banner:
            self._capture_print(
                f"[{self.theme.brand}]  .--.      [/]\n"
                f"[{self.theme.brand}] ( () ) agent-driver[/]\n"
                f"[{self.theme.brand}]  '--'      [/]"
            )
        self._capture_print(
            Panel(
                body,
                border_style=self.theme.prompt_border,
                title="chat",
                title_align="left",
            )
        )
        self._capture_print(
            f"[{self.theme.subtle}]! for bash {DOT} / for commands {DOT} @ for files {DOT} esc to interrupt[/]"
        )
        self._capture_print(
            f"[{self.theme.subtle}]/reset clears memory {DOT} /history shows transcript {DOT} ctrl-c twice exits[/]"
        )

    def assistant_prefix(self) -> str:
        if not _RICH_AVAILABLE:
            return self._fallback.assistant_prefix()
        return f"\n{BLACK_CIRCLE}\n"

    def emit_assistant_delta(self, delta: str) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_assistant_delta(delta)
            return
        self.output(delta)

    def emit_assistant_tail(self, text: str) -> None:
        if not text:
            return
        if not _RICH_AVAILABLE:
            self._fallback.emit_assistant_tail(text)
            return
        lines = text.splitlines() or [text]
        indented = "\n".join(f"  {line}" if line else "" for line in lines)
        self._capture_print(Markdown(indented))

    def emit_tool(self, compact: str) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_tool(compact)
            return
        self._capture_print(
            f"[{self.theme.accent}]{BLACK_CIRCLE}[/] [bold]{compact}[/]"
        )

    def emit_tool_card(
        self,
        *,
        name: str,
        args_summary: str,
        status: str | None,
        result_summary: str | None,
        truncated: bool | None = None,
        error_code: str | None = None,
    ) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_tool_card(
                name=name,
                args_summary=args_summary,
                status=status,
                result_summary=result_summary,
                truncated=truncated,
                error_code=error_code,
            )
            return
        summary = result_summary or "ok"
        truncated_badge = f" [{self.theme.warning}]truncated[/]" if truncated else ""
        if status in {"denied", "error"}:
            badge = f"[{self.theme.error}]status={status}[/]"
            if error_code:
                summary = f"{summary} (code={error_code})"
            self._capture_print(
                f"[{self.theme.accent}]{BLACK_CIRCLE}[/] [bold]{name}({args_summary})[/] {badge}{truncated_badge}\n"
                f"[{self.theme.error}]  ⎿ reason: {summary}[/]"
            )
            return
        status_suffix = f" [{self.theme.subtle}]status={status}[/]" if status else ""
        self._capture_print(
            f"[{self.theme.accent}]{BLACK_CIRCLE}[/] [bold]{name}({args_summary})[/]{status_suffix}{truncated_badge}\n"
            f"[{self.theme.subtle}]  ⎿ {summary}[/]"
        )

    def emit_warning(self, compact: str) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_warning(compact)
            return
        self._capture_print(f"[{self.theme.warning}]{BRANCH} {compact}[/]")

    def emit_event(self, compact: str) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_event(compact)
            return
        style = self.theme.error if "run_failed" in compact else self.theme.subtle
        self._capture_print(f"[{style}]{BRANCH} {compact}[/]")

    def emit_run_summary(
        self, run_id: str, tools_used: int, warnings_seen: int, duration_seconds: float | None = None
    ) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_run_summary(run_id, tools_used, warnings_seen, duration_seconds)
            return
        turn = run_id.rsplit("_", 1)[-1] if "_" in run_id else run_id
        duration_text = f" {int(duration_seconds)}s " if duration_seconds is not None else " "
        self._capture_print(
            f"[{self.theme.subtle}]run #{turn} {DOT}{duration_text}{DOT} {tools_used} tools {DOT} {warnings_seen} warn[/]"
        )

    def emit_help(self, text: str) -> None:
        self.emit_raw(text)

    def emit_error_card(self, *, title: str, reason: str, hint: str | None = None) -> None:
        if not _RICH_AVAILABLE:
            self._fallback.emit_error_card(title=title, reason=reason, hint=hint)
            return
        details = f"[{self.theme.error}]reason:[/] {reason}"
        if hint:
            details += f"\n[{self.theme.warning}]hint:[/] {hint}"
        self._capture_print(
            Panel(
                details,
                title=title,
                title_align="left",
                border_style=self.theme.error,
            )
        )

    def emit_raw(self, text: str) -> None:
        self.output(text)

    def _capture_print(self, *renderables: object) -> None:
        buffer = StringIO()
        console = Console(
            file=buffer,
            record=False,
            force_terminal=False,
            color_system="standard",
            width=shutil.get_terminal_size((120, 24)).columns,
        )
        console.print(*renderables)
        rendered = buffer.getvalue()
        if rendered:
            self.output(rendered)


def build_renderer(*, output: Callable[[str], None], ui_mode: str) -> ChatRenderer:
    if ui_mode == "rich":
        return RichRenderer(output=output)
    return PlainRenderer(output=output)


__all__ = ["ChatRenderer", "PlainRenderer", "RichRenderer", "build_renderer"]
