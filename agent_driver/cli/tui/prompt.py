"""Prompt-toolkit based interactive chat input."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from agent_driver.cli.tui.glyphs import POINTER

try:  # pragma: no cover - optional dependency
    from prompt_toolkit import ANSI, PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.shortcuts.prompt import CompleteStyle

    _PROMPT_TOOLKIT_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    PromptSession = object  # type: ignore[assignment,misc]
    FileHistory = object  # type: ignore[assignment,misc]
    KeyBindings = object  # type: ignore[assignment,misc]
    Completer = object  # type: ignore[assignment,misc]
    Completion = object  # type: ignore[assignment,misc]
    ANSI = object  # type: ignore[assignment,misc]
    CompleteStyle = object  # type: ignore[assignment,misc]
    _PROMPT_TOOLKIT_AVAILABLE = False


SLASH_COMMANDS = (
    "/help",
    "/exit",
    "/quit",
    "/clear",
    "/reset",
    "/runs",
    "/sessions",
    "/history",
    "/resume",
    "/tools",
    "/model",
    "/provider",
    "/limits",
    "/debug",
    "/save",
    "/export",
    "/doctor",
    "/approve",
    "/reject",
    "/cancel",
    "/clarify",
    "/replay",
    "/tail",
)


class SlashCommandCompleter(Completer):
    """Completer for slash commands and @path references."""

    def get_completions(self, document, complete_event):  # type: ignore[override]
        _ = complete_event
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            yield from self._path_completions(document)
            return
        for command in SLASH_COMMANDS:
            if command.startswith(text):
                yield Completion(command, start_position=-len(text))

    def _path_completions(self, document):
        before = document.text_before_cursor
        marker = before.rfind("@")
        if marker < 0:
            return
        token = before[marker + 1 :]
        if any(ch.isspace() for ch in token):
            return
        token_path = Path(token) if token else Path(".")
        base_dir = token_path.parent if token_path.name else token_path
        prefix = token_path.name
        search_dir = Path.cwd() / base_dir
        if not search_dir.exists() or not search_dir.is_dir():
            return
        for name in sorted(os.listdir(search_dir)):
            if prefix and not name.startswith(prefix):
                continue
            candidate = (base_dir / name) if str(base_dir) not in {".", ""} else Path(name)
            candidate_path = search_dir / name
            suffix = "/" if candidate_path.is_dir() else ""
            completion = f"@{candidate.as_posix()}{suffix}"
            yield Completion(completion, start_position=-(len(token) + 1))


class ChatPromptSession:
    """Interactive prompt with history, completions and footer toolbar."""

    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str | None,
        session_id: str,
    ) -> None:
        if not _PROMPT_TOOLKIT_AVAILABLE:
            raise RuntimeError("prompt_toolkit is not installed for rich chat mode")
        history_path = (
            Path.home() / ".config" / "agent-driver" / "chat-history"
        )
        history_path.parent.mkdir(parents=True, exist_ok=True)

        self._provider_name = provider_name
        self._model_name = model_name or "default"
        self._session_id = session_id
        self._input_tokens = 0
        self._output_tokens = 0
        self._pressure_state = "ok"
        self._budget_warning: str | None = None
        self._continuation_lines: list[str] = []

        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("c-j")
        def _ctrl_newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @bindings.add("escape", "enter")
        def _alt_newline(event) -> None:
            event.current_buffer.insert_text("\n")

        self._session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=SlashCommandCompleter(),
            complete_style=CompleteStyle.COLUMN,
            key_bindings=bindings,
        )

    def _prompt_message(self) -> ANSI:
        top = "╭" + "─" * self._frame_inner_width() + "╮\n"
        if self._continuation_lines:
            message = "│   "
        else:
            message = f"{top}│ \x1b[96m{POINTER}\x1b[0m "
        return ANSI(message)

    def prompt_closing_frame(self) -> str:
        return "╰" + ("─" * self._frame_inner_width()) + "╯\n"

    def _prompt_kwargs(self) -> dict[str, object]:
        return {
            "multiline": True,
            "bottom_toolbar": self._toolbar,
            "mouse_support": False,
            "prompt_continuation": lambda width, line_number, is_soft_wrap: "│   ",
        }

    def prompt(self) -> str:
        return self._session.prompt(
            self._prompt_message(),
            **self._prompt_kwargs(),
        )

    async def prompt_async(self) -> str:
        while True:
            line = await self._session.prompt_async(
                self._prompt_message(),
                **self._prompt_kwargs(),
            )
            if line.endswith("\\"):
                self._continuation_lines.append(line[:-1].rstrip())
                continue
            if self._continuation_lines:
                self._continuation_lines.append(line)
                full = "\n".join(self._continuation_lines)
                self._continuation_lines.clear()
                return full
            return line

    def _toolbar(self) -> str:
        token_summary = f"{self._format_tokens(self._input_tokens)}↑/{self._format_tokens(self._output_tokens)}↓"
        pressure = f" · ctx={self._pressure_state}" if self._pressure_state != "ok" else ""
        budget = f" · budget={self._budget_warning}" if self._budget_warning else ""
        return (
            f" esc to interrupt · ? for shortcuts · provider={self._provider_name}"
            f" · model={self._model_name} · tokens={token_summary}"
            f"{pressure}{budget} · session={self._session_id}"
        )

    def set_usage(self, *, input_tokens: int, output_tokens: int) -> None:
        self._input_tokens = max(0, input_tokens)
        self._output_tokens = max(0, output_tokens)

    def set_pressure(self, state: str | None) -> None:
        if not state:
            self._pressure_state = "ok"
            return
        self._pressure_state = state

    def set_budget_warning(self, warning: str | None) -> None:
        self._budget_warning = warning.strip() if isinstance(warning, str) and warning.strip() else None

    @staticmethod
    def _format_tokens(value: int) -> str:
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)

    @staticmethod
    def _frame_inner_width() -> int:
        columns = shutil.get_terminal_size((100, 24)).columns
        return max(40, columns - 2)


__all__ = ["ChatPromptSession", "SLASH_COMMANDS"]
