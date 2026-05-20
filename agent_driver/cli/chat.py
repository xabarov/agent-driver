"""Interactive terminal chat loop for agent-driver CLI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid

from agent_driver.adapters import cli_replay_lines, cli_tail_lines
from agent_driver.contracts import ToolManifest
from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.cli.chat_stream import render_chat_stream
from agent_driver.cli.tui.prompt import ChatPromptSession
from agent_driver.cli.tui.renderer import build_renderer
from agent_driver.llm.tool_call_parser import strip_text_form_tool_calls
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.sdk import Agent
from agent_driver.cli.sessions import SessionStore
from agent_driver.tools.builtin.python import python_tool_runtime_facts

try:  # pragma: no cover - optional dependency
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:  # pragma: no cover - optional dependency
    patch_stdout = None  # type: ignore[assignment]

_EXIT_COMMANDS = {"exit", "quit"}
_CTRL_C_WINDOW_SECONDS = 2.0


@dataclass(slots=True)
class ChatSessionState:
    """In-memory chat session state for one interactive process."""

    session_id: str = field(default_factory=lambda: f"session_{uuid.uuid4().hex[:8]}")
    thread_id: str = field(default_factory=lambda: f"thread_{uuid.uuid4().hex[:8]}")
    run_ids: list[str] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    turn_index: int = 0
    debug_tool_protocol: bool = False

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


def _transcript_to_messages(transcript: list[tuple[str, str]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for role, text in transcript:
        content = text.strip()
        if not content:
            continue
        if role == "user":
            messages.append(ChatMessage(role=ChatRole.USER, content=content))
        elif role == "assistant":
            messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=content))
    return messages


def _print_help(output: Callable[[str], None]) -> None:
    output(
        "Commands: /help /exit /quit /clear /reset /runs /sessions /history "
        "/resume <session_id> /tools [verbose] /model /provider /limits "
        "/debug on|off /save [path] /export [path] /doctor "
        "/approve <run_id> <interrupt_id> /reject <run_id> <interrupt_id> [message] "
        "/cancel <run_id> <interrupt_id> /clarify <run_id> <interrupt_id> <message> "
        "/replay [run_id] /tail [run_id] [last_n]\n"
    )


def _resolve_run_id(args: list[str], state: ChatSessionState) -> str | None:
    if args:
        return args[0]
    return state.last_run_id


async def _handle_local_command(
    *,
    agent: Agent,
    command: str,
    args: list[str],
    state: ChatSessionState,
    event_log: RuntimeEventLog,
    session_store: SessionStore,
    provider_name: str,
    model_name: str | None,
    max_steps: int | None,
    max_tool_calls: int | None,
    deadline_seconds: float | None,
    selected_manifests: list[ToolManifest],
    output: Callable[[str], None],
    clear_screen: Callable[[], None] | None = None,
    welcome: Callable[[], None] | None = None,
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
        if clear_screen is not None:
            clear_screen()
        output("chat> cleared\n")
        if welcome is not None:
            welcome()
        return True
    if command == "reset":
        state.transcript.clear()
        state.run_ids.clear()
        state.thread_id = f"thread_{uuid.uuid4().hex[:8]}"
        state.turn_index = 0
        output(f"chat> memory reset thread={state.thread_id}\n")
        return True
    if command == "runs":
        if not state.run_ids:
            output("chat> no runs yet\n")
            return True
        for run_id in state.run_ids:
            output(f"run> {run_id}\n")
        return True
    if command == "sessions":
        sessions = session_store.list_sessions()
        if not sessions:
            output("session> none\n")
            return True
        for item in sessions:
            output(f"session> {item.session_id} thread={item.thread_id} runs={len(item.run_ids)}\n")
        return True
    if command == "history":
        if not state.transcript:
            output("history> empty\n")
            return True
        for role, text in state.transcript[-30:]:
            compact = text.replace("\n", " ")
            if len(compact) > 80:
                compact = f"{compact[:80].rstrip()}..."
            output(f"{role}> {compact}\n")
        return True
    if command == "resume":
        if not args:
            output("chat> /resume requires session_id\n")
            return True
        record = session_store.get(args[0])
        if record is None:
            output(f"chat> unknown session '{args[0]}'\n")
            return True
        state.session_id = record.session_id
        state.thread_id = record.thread_id
        state.run_ids = list(record.run_ids)
        state.transcript = list(record.transcript)
        state.turn_index = len(state.run_ids)
        output(f"chat> resumed session={state.session_id} thread={state.thread_id}\n")
        return True
    if command == "tools":
        if not selected_manifests:
            output("tools> none\n")
            return True
        verbose = bool(args and args[0].lower() == "verbose")
        if verbose:
            for manifest in selected_manifests:
                output(
                    "tools> "
                    f"{manifest.name} risk={manifest.risk.value} "
                    f"side_effect={manifest.side_effect.value} "
                    f"description={manifest.description}\n"
                )
            return True
        names = ", ".join(manifest.name for manifest in selected_manifests)
        output(f"tools> {names}\n")
        return True
    if command == "model":
        output(f"model> {model_name or 'default'}\n")
        return True
    if command == "provider":
        output(f"provider> {provider_name}\n")
        return True
    if command == "limits":
        output(
            "limits> "
            f"max_steps={max_steps} max_tool_calls={max_tool_calls} "
            f"deadline_seconds={deadline_seconds}\n"
        )
        return True
    if command == "debug":
        if not args:
            output(f"debug> {'on' if state.debug_tool_protocol else 'off'}\n")
            return True
        value = args[0].lower()
        if value in {"on", "1", "true"}:
            state.debug_tool_protocol = True
            output("debug> on\n")
        elif value in {"off", "0", "false"}:
            state.debug_tool_protocol = False
            output("debug> off\n")
        else:
            output("chat> /debug expects on|off\n")
        return True
    if command == "doctor":
        status = await agent.runner.deps.provider.healthcheck()
        python_settings = getattr(agent.runner.config, "python_tool", None)
        python_imports = (
            python_tool_runtime_facts(python_settings).imports_short
            if python_settings is not None and getattr(python_settings, "enabled", False)
            else "disabled"
        )
        tools = ", ".join(manifest.name for manifest in selected_manifests) or "none"
        last_signal = "none"
        run_id = state.last_run_id
        if run_id is not None:
            for event in reversed(list(event_log.list_for_run(run_id))):
                event_type = (
                    event.type.value
                    if hasattr(event.type, "value")
                    else str(event.type)
                )
                payload = event.payload if isinstance(event.payload, dict) else {}
                if event_type == "run_failed":
                    last_signal = f"run_failed:{payload.get('reason', 'unknown')}"
                    break
                if event_type == "interrupt_requested":
                    last_signal = f"interrupt_requested:{payload.get('reason', 'unknown')}"
                    break
                if event_type == "run_completed":
                    last_signal = "final_answered"
                    break
        output(
            "doctor> "
            f"name={status.provider_name} healthy={status.healthy} "
            f"configured={status.configured} latency_ms={status.latency_ms}\n"
        )
        output(f"doctor> limits max_steps={max_steps} max_tool_calls={max_tool_calls} deadline_seconds={deadline_seconds}\n")
        output(f"doctor> tools {tools}\n")
        output(f"doctor> python_imports {python_imports}\n")
        output(f"doctor> last_signal {last_signal}\n")
        return True
    if command == "save":
        record = session_store.upsert(
            session_id=state.session_id,
            thread_id=state.thread_id,
            run_ids=state.run_ids,
            transcript=state.transcript,
        )
        target = args[0] if args else str(session_store.path)
        if args:
            output_path = Path(args[0])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        "session_id": record.session_id,
                        "thread_id": record.thread_id,
                        "run_ids": list(record.run_ids),
                        "transcript": [list(item) for item in record.transcript],
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                    },
                    ensure_ascii=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
        output(f"save> {target}\n")
        return True
    if command == "export":
        export_path = Path(args[0]) if args else Path.cwd() / f"{state.session_id}.md"
        lines = [f"# Session {state.session_id}", ""]
        for role, text in state.transcript:
            lines.append(f"## {role}")
            lines.append(text)
            lines.append("")
        export_path.write_text("\n".join(lines), encoding="utf-8")
        output(f"export> {export_path}\n")
        return True
    if command in {"approve", "reject", "cancel", "clarify"}:
        if len(args) < 2:
            output(f"chat> /{command} requires <run_id> <interrupt_id>\n")
            return True
        run_id, interrupt_id = args[0], args[1]
        if command == "approve":
            _ = await agent.approve(run_id=run_id, interrupt_id=interrupt_id)
        elif command == "reject":
            message = " ".join(args[2:]) if len(args) >= 3 else None
            _ = await agent.reject(
                run_id=run_id, interrupt_id=interrupt_id, message=message
            )
        elif command == "cancel":
            _ = await agent.cancel(run_id=run_id, interrupt_id=interrupt_id)
        else:
            if len(args) < 3:
                output("chat> /clarify requires message after interrupt_id\n")
                return True
            _ = await agent.clarify(
                run_id=run_id, interrupt_id=interrupt_id, message=" ".join(args[2:])
            )
        output(f"resume> {command} ok run_id={run_id}\n")
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


async def _run_shell_bang(command_text: str, output: Callable[[str], None]) -> None:
    output(f"● Bash(!{command_text})\n")
    process = await asyncio.create_subprocess_shell(
        command_text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if stdout:
        output(f"  ⎿ {stdout.decode(errors='replace').rstrip()}\n")
    if stderr:
        output(f"  ⎿ stderr: {stderr.decode(errors='replace').rstrip()}\n")
    if process.returncode not in {0, None}:
        output(f"  ⎿ exit_code={process.returncode}\n")


async def run_chat_session(
    *,
    agent: Agent,
    event_log: RuntimeEventLog,
    agent_id: str,
    graph_preset: str,
    stream_poll_interval_ms: int,
    max_steps: int | None = None,
    max_tool_calls: int | None = None,
    deadline_seconds: float | None = None,
    debug_tool_protocol: bool = False,
    resume_session_id: str | None = None,
    session_store: SessionStore | None = None,
    provider_name: str = "provider",
    model_name: str | None = None,
    selected_manifests: list[ToolManifest] | None = None,
    ui_mode: str | None = None,
    animate: bool = False,
    input_reader: Callable[[str], str] | None = None,
    output: Callable[[str], None] | None = None,
) -> int:
    """Run interactive chat loop until explicit exit or EOF."""
    write = output or (lambda text: print(text, end="", flush=True))
    requested_mode = ui_mode or ("rich" if animate else "plain")
    effective_mode = (
        "plain"
        if input_reader is not None or output is not None
        else requested_mode
    )
    if effective_mode == "rich" and not sys.stdout.isatty():
        effective_mode = "plain"
    renderer = build_renderer(output=write, ui_mode=effective_mode)
    store = session_store or SessionStore()
    manifests = list(selected_manifests or [])
    state = ChatSessionState(debug_tool_protocol=debug_tool_protocol)
    if resume_session_id:
        record = store.get(resume_session_id)
        if record is not None:
            state = ChatSessionState(
                session_id=record.session_id,
                thread_id=record.thread_id,
                run_ids=list(record.run_ids),
                transcript=list(record.transcript),
                turn_index=len(record.run_ids),
                debug_tool_protocol=debug_tool_protocol,
            )

    session_input_tokens = 0
    session_output_tokens = 0
    trim_max_chars = int(getattr(agent.runner.config, "trim_max_chars", 6000))
    trim_max_messages = getattr(agent.runner.config, "trim_max_messages", 24)

    def _stream_context():
        if prompt_session is not None and patch_stdout is not None:
            return patch_stdout(raw=True)
        return nullcontext()

    def _detect_git_branch() -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
        except Exception:
            return None
        branch = result.stdout.strip()
        if result.returncode != 0 or not branch:
            return None
        return branch

    def _emit_welcome() -> None:
        python_backend = None
        python_allowed_imports = None
        if any(manifest.name == "python" for manifest in manifests):
            python_settings = getattr(agent.runner.config, "python_tool", None)
            raw_backend = getattr(python_settings, "backend", None)
            if isinstance(raw_backend, str) and raw_backend.strip():
                python_backend = raw_backend
            if python_settings is not None:
                facts = python_tool_runtime_facts(python_settings)
                python_allowed_imports = facts.imports_short
        renderer.welcome(
            provider_name=provider_name,
            model_name=model_name,
            session_id=state.session_id,
            thread_id=state.thread_id,
            tools_count=len(manifests),
            python_backend=python_backend,
            python_allowed_imports=python_allowed_imports,
            limits_summary=(
                f"steps={max_steps} tools={max_tool_calls} "
                f"deadline={deadline_seconds}s"
                if deadline_seconds is not None
                else f"steps={max_steps} tools={max_tool_calls} deadline=none"
            ),
            cwd=str(Path.cwd()),
            git_branch=_detect_git_branch(),
            mode_label="chat+debug" if state.debug_tool_protocol else "chat",
        )

    def _clear_screen() -> None:
        renderer.emit_raw("\x1b[H\x1b[2J")
    _emit_welcome()
    prompt_session: ChatPromptSession | None = None
    if effective_mode == "rich" and input_reader is None and sys.stdin.isatty():
        try:
            prompt_session = ChatPromptSession(
                provider_name=provider_name,
                model_name=model_name,
                session_id=state.session_id,
            )
        except RuntimeError:
            renderer.emit_raw("chat> rich prompt disabled: prompt_toolkit is unavailable\n")
            effective_mode = "plain"
    last_keyboard_interrupt = 0.0
    while True:
        with _stream_context():
            try:
                if input_reader is not None:
                    raw = input_reader("you> ")
                elif prompt_session is not None:
                    raw = await prompt_session.prompt_async()
                else:
                    raw = input("you> ")
            except EOFError:
                renderer.emit_raw("chat> eof\n")
                return 0
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_keyboard_interrupt <= _CTRL_C_WINDOW_SECONDS:
                    renderer.emit_raw("\nchat> interrupted\n")
                    return 0
                last_keyboard_interrupt = now
                renderer.emit_raw("\nchat> press Ctrl+C again within 2s to exit\n")
                continue
            text = raw.strip()
            if not text:
                continue
            if prompt_session is not None and renderer.rich_enabled:
                renderer.emit_raw(prompt_session.prompt_closing_frame())
            if text.startswith("!") and len(text) > 1:
                await _run_shell_bang(text[1:].strip(), renderer.emit_raw)
                continue
            parsed = parse_chat_command(text)
            if parsed is not None:
                command, args = parsed
                keep_running = await _handle_local_command(
                    agent=agent,
                    command=command,
                    args=args,
                    state=state,
                    event_log=event_log,
                    session_store=store,
                    provider_name=provider_name,
                    model_name=model_name,
                    max_steps=max_steps,
                    max_tool_calls=max_tool_calls,
                    deadline_seconds=deadline_seconds,
                    selected_manifests=manifests,
                    output=renderer.emit_raw,
                    clear_screen=_clear_screen,
                    welcome=_emit_welcome,
                )
                if not keep_running:
                    return 0
                if prompt_session is not None and command in {"clear", "reset"}:
                    prompt_session.set_pressure(None)
                    prompt_session.set_budget_warning(None)
                continue
            run_id = state.next_run_id()
            state.run_ids.append(run_id)
            state.transcript.append(("user", text))
            messages = _transcript_to_messages(state.transcript)
            budget_parts: list[str] = []
            if isinstance(trim_max_messages, int) and trim_max_messages > 0 and len(messages) > trim_max_messages:
                budget_parts.append(f"messages {len(messages)}/{trim_max_messages}")
            message_chars = sum(len(item.content) for item in messages)
            if trim_max_chars > 0 and message_chars > trim_max_chars:
                budget_parts.append(f"chars {message_chars}/{trim_max_chars}")
            if prompt_session is not None:
                prompt_session.set_budget_warning(" | ".join(budget_parts) if budget_parts else None)
            stream = agent.stream(
                AgentRunInput(
                    input=text,
                    messages=messages,
                    run_id=run_id,
                    thread_id=state.thread_id,
                    agent_id=agent_id,
                    graph_preset=graph_preset,
                    stream=True,
                    max_steps=max_steps,
                    max_tool_calls=max_tool_calls,
                    deadline_seconds=deadline_seconds,
                    app_metadata={
                        "stream_poll_interval_ms": stream_poll_interval_ms,
                        "chat_mode": True,
                        "debug_tool_protocol": state.debug_tool_protocol,
                    },
                )
            )
            assistant_text, input_tokens, output_tokens, pressure_state = await render_chat_stream(
                stream=stream,
                output=renderer.emit_raw,
                run_id=run_id,
                renderer=renderer,
                animate=effective_mode == "rich",
            )
            session_input_tokens += input_tokens
            session_output_tokens += output_tokens
            if prompt_session is not None:
                prompt_session.set_usage(
                    input_tokens=session_input_tokens,
                    output_tokens=session_output_tokens,
                )
                prompt_session.set_pressure(pressure_state)
            if assistant_text:
                state.transcript.append(
                    ("assistant", strip_text_form_tool_calls(assistant_text))
                )
            store.upsert(
                session_id=state.session_id,
                thread_id=state.thread_id,
                run_ids=state.run_ids,
                transcript=state.transcript,
            )


__all__ = [
    "ChatSessionState",
    "parse_chat_command",
    "render_chat_stream",
    "run_chat_session",
]
