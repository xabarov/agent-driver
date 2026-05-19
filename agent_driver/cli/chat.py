"""Interactive terminal chat loop for agent-driver CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import shlex
import uuid

from agent_driver.adapters import cli_replay_lines, cli_tail_lines
from agent_driver.contracts import AgentRunInput, RunStreamEvent
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.sdk import Agent

_EXIT_COMMANDS = {"exit", "quit"}
_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}


@dataclass(slots=True)
class ChatSessionState:
    """In-memory chat session state for one interactive process."""

    session_id: str = field(default_factory=lambda: f"session_{uuid.uuid4().hex[:8]}")
    thread_id: str = field(default_factory=lambda: f"thread_{uuid.uuid4().hex[:8]}")
    run_ids: list[str] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    turn_index: int = 0

    def next_run_id(self) -> str:
        """Generate deterministic run id prefix for one chat turn."""
        self.turn_index += 1
        return f"run_chat_{self.session_id}_{self.turn_index:04d}"

    @property
    def last_run_id(self) -> str | None:
        """Return most recent run id, if available."""
        if not self.run_ids:
            return None
        return self.run_ids[-1]


def parse_chat_command(raw: str) -> tuple[str, list[str]] | None:
    """Parse slash command line into command name and args."""
    text = raw.strip()
    if not text.startswith("/"):
        return None
    parts = shlex.split(text[1:])
    if not parts:
        return ("help", [])
    return (parts[0].lower(), parts[1:])


def _format_compact_event(event: RunStreamEvent) -> str | None:
    name = event.event
    if name in {"run_started", "llm_call_started", "llm_call_completed", "checkpoint_saved"}:
        return None
    if name in _TERMINAL_EVENTS:
        reason = event.data.get("reason")
        return f"run {name}" if reason is None else f"run {name} reason={reason}"
    if name in {"tool_call_started", "tool_call_completed"}:
        tool_name = event.data.get("tool_name", "?")
        status = event.data.get("status")
        suffix = f" status={status}" if status else ""
        return f"tool {name} tool={tool_name}{suffix}"
    if name == "warning":
        return f"warning kind={event.data.get('kind', 'warning')}"
    if name in {"interrupt_requested", "run_paused"}:
        reason = event.data.get("reason", "unknown")
        return f"interrupt reason={reason}"
    return f"event {name}"


async def render_chat_stream(
    *,
    stream,
    output: Callable[[str], None],
    run_id: str,
) -> str:
    """Render stream to chat-oriented output and return assistant text."""
    assistant_parts: list[str] = []
    token_line_open = False
    saw_terminal = False
    async for event in stream:
        if event.event == "token_delta":
            if not token_line_open:
                output("assistant> ")
                token_line_open = True
            delta = str(event.data.get("delta_text") or "")
            if delta:
                assistant_parts.append(delta)
                output(delta)
            continue
        compact = _format_compact_event(event)
        if compact is None:
            continue
        if token_line_open:
            output("\n")
            token_line_open = False
        output(f"event> {compact}\n")
        if event.event in _TERMINAL_EVENTS:
            saw_terminal = True
    if token_line_open:
        output("\n")
    assistant_text = "".join(assistant_parts)
    if not assistant_text and not saw_terminal:
        output("assistant> [no textual response]\n")
    output(f"run> {run_id}\n")
    return assistant_text


def _print_help(output: Callable[[str], None]) -> None:
    output("Commands: /help /exit /quit /clear /runs /replay [run_id] /tail [run_id] [last_n]\n")


def _resolve_run_id(args: list[str], state: ChatSessionState) -> str | None:
    if args:
        return args[0]
    return state.last_run_id


def _handle_local_command(
    *,
    command: str,
    args: list[str],
    state: ChatSessionState,
    event_log: RuntimeEventLog,
    output: Callable[[str], None],
) -> bool:
    if command == "help":
        _print_help(output)
        return True
    if command in _EXIT_COMMANDS:
        output("chat> bye\n")
        return False
    if command == "clear":
        state.transcript.clear()
        state.run_ids.clear()
        output("chat> cleared\n")
        return True
    if command == "runs":
        if not state.run_ids:
            output("chat> no runs yet\n")
            return True
        for run_id in state.run_ids:
            output(f"run> {run_id}\n")
        return True
    if command == "replay":
        run_id = _resolve_run_id(args, state)
        if run_id is None:
            output("chat> replay requires run_id or existing session run\n")
            return True
        for line in cli_replay_lines(event_log, run_id=run_id):
            output(f"{line}\n")
        return True
    if command == "tail":
        run_id = _resolve_run_id(args, state)
        if run_id is None:
            output("chat> tail requires run_id or existing session run\n")
            return True
        last_n = 20
        if len(args) >= 2:
            try:
                last_n = int(args[1])
            except ValueError:
                output("chat> tail last_n must be integer\n")
                return True
        for line in cli_tail_lines(event_log, run_id=run_id, last_n=last_n):
            output(f"{line}\n")
        return True
    output(f"chat> unknown command '/{command}'\n")
    return True


async def run_chat_session(
    *,
    agent: Agent,
    event_log: RuntimeEventLog,
    agent_id: str,
    graph_preset: str,
    stream_poll_interval_ms: int,
    input_reader: Callable[[str], str] | None = None,
    output: Callable[[str], None] | None = None,
) -> int:
    """Run interactive chat loop until explicit exit or EOF."""
    read = input_reader or input
    write = output or (lambda text: print(text, end=""))
    state = ChatSessionState()
    write(f"chat> session={state.session_id} thread={state.thread_id}\n")
    write("chat> type /help for commands\n")
    while True:
        try:
            raw = read("you> ")
        except EOFError:
            write("chat> eof\n")
            return 0
        except KeyboardInterrupt:
            write("\nchat> interrupted\n")
            return 0
        text = raw.strip()
        if not text:
            continue
        parsed = parse_chat_command(text)
        if parsed is not None:
            command, args = parsed
            keep_running = _handle_local_command(
                command=command,
                args=args,
                state=state,
                event_log=event_log,
                output=write,
            )
            if not keep_running:
                return 0
            continue
        run_id = state.next_run_id()
        state.run_ids.append(run_id)
        state.transcript.append(("user", text))
        stream = agent.stream(
            AgentRunInput(
                input=text,
                run_id=run_id,
                thread_id=state.thread_id,
                agent_id=agent_id,
                graph_preset=graph_preset,
                stream=True,
                app_metadata={"stream_poll_interval_ms": stream_poll_interval_ms},
            )
        )
        assistant_text = await render_chat_stream(
            stream=stream,
            output=write,
            run_id=run_id,
        )
        if assistant_text:
            state.transcript.append(("assistant", assistant_text))


__all__ = [
    "ChatSessionState",
    "parse_chat_command",
    "render_chat_stream",
    "run_chat_session",
]
