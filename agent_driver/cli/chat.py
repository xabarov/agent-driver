"""Interactive terminal chat loop for agent-driver CLI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext, suppress
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
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk import Agent
from agent_driver.cli.sessions import SessionStore
from agent_driver.cli.tui.plan_panel import format_plan_panel, plan_progress_footer
from agent_driver.tools.builtin.python import python_tool_runtime_facts

try:  # pragma: no cover - optional dependency
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:  # pragma: no cover - optional dependency
    patch_stdout = None  # type: ignore[assignment]

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
    planning_state: dict[str, object] | None = None
    workspace_cwd: Path | None = None

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


def _provider_check_error_label(exc: Exception) -> str:
    name = type(exc).__name__
    if name == "SSLError":
        return "ssl_error"
    if name == "HTTPStatusError":
        return "http_status_error"
    if name == "ConnectTimeout":
        return "connect_timeout"
    if name == "ReadTimeout":
        return "read_timeout"
    return name.lower()


async def _shutdown_chat_resources(
    agent: Agent,
    *,
    prompt_session: ChatPromptSession | None = None,
) -> None:
    """Release TUI and python-tool worker processes before process exit."""
    if prompt_session is not None:
        with suppress(Exception):
            prompt_session.close()
    backend = agent.runner.deps.python_backend
    if backend is None:
        return
    aclose = getattr(backend, "aclose", None)
    if callable(aclose):
        with suppress(Exception):
            await aclose()


def _doctor_last_signal(state: ChatSessionState, event_log: RuntimeEventLog) -> str:
    run_id = state.last_run_id
    if run_id is None:
        return "none"
    for event in reversed(list(event_log.list_for_run(run_id))):
        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event_type == "run_failed":
            return f"run_failed:{payload.get('reason', 'unknown')}"
        if event_type == "interrupt_requested":
            return f"interrupt_requested:{payload.get('reason', 'unknown')}"
        if event_type == "run_completed":
            return "final_answered"
    return "none"


def _print_help(output: Callable[[str], None]) -> None:
    output(
        "Commands: /help /exit /quit /clear /reset /plan /runs /sessions /history "
        "/resume <session_id> /tools [verbose] /workspace [path] /cd <path> "
        "/model /provider /limits "
        "/debug on|off /save [path] /export [path] /doctor "
        "/approve <run_id> <interrupt_id> /reject <run_id> <interrupt_id> [message] "
        "/cancel <run_id> <interrupt_id> /clarify <run_id> <interrupt_id> <message> "
        "/replay [run_id] /tail [run_id] [last_n]\n"
    )


def _resolve_workspace_path(raw: str, current: Path | None) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ((current or Path.cwd()) / path).resolve()
    else:
        path = path.resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"workspace is not an existing directory: {path}")
    return path


def _resolve_run_id(args: list[str], state: ChatSessionState) -> str | None:
    if args:
        return args[0]
    return state.last_run_id


@dataclass(slots=True)
class _ChatCommandContext:
    """Pass-through state shared by every local slash-command handler."""

    agent: Agent
    state: ChatSessionState
    event_log: RuntimeEventLog
    session_store: SessionStore
    provider_name: str
    model_name: str | None
    max_steps: int | None
    max_tool_calls: int | None
    deadline_seconds: float | None
    selected_manifests: list[ToolManifest]
    output: Callable[[str], None]
    clear_screen: Callable[[], None] | None = None
    welcome: Callable[[], None] | None = None
    renderer: object | None = None


async def _cmd_help(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    _print_help(ctx.output)
    return True


async def _cmd_exit(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    ctx.output("chat> bye\n")
    return False


async def _cmd_clear(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    ctx.state.transcript.clear()
    ctx.state.run_ids.clear()
    ctx.state.planning_state = None
    if ctx.renderer is not None and hasattr(ctx.renderer, "clear_plan_panel"):
        ctx.renderer.clear_plan_panel()
    if ctx.clear_screen is not None:
        ctx.clear_screen()
    ctx.output("chat> cleared\n")
    if ctx.welcome is not None:
        ctx.welcome()
    return True


async def _cmd_reset(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    ctx.state.transcript.clear()
    ctx.state.run_ids.clear()
    ctx.state.planning_state = None
    if ctx.renderer is not None and hasattr(ctx.renderer, "clear_plan_panel"):
        ctx.renderer.clear_plan_panel()
    ctx.state.thread_id = f"thread_{uuid.uuid4().hex[:8]}"
    ctx.state.turn_index = 0
    ctx.output(f"chat> memory reset thread={ctx.state.thread_id}\n")
    return True


async def _cmd_plan(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    if isinstance(ctx.state.planning_state, dict) and ctx.state.planning_state.get(
        "todos"
    ):
        ctx.output(f"{format_plan_panel(ctx.state.planning_state)}\n")
    else:
        ctx.output("plan> empty (no todos in this session)\n")
    return True


async def _cmd_runs(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    if not ctx.state.run_ids:
        ctx.output("chat> no runs yet\n")
        return True
    for run_id in ctx.state.run_ids:
        ctx.output(f"run> {run_id}\n")
    return True


async def _cmd_sessions(
    ctx: _ChatCommandContext, command: str, args: list[str]
) -> bool:
    sessions = ctx.session_store.list_sessions()
    if not sessions:
        ctx.output("session> none\n")
        return True
    for item in sessions:
        ctx.output(
            f"session> {item.session_id} thread={item.thread_id} runs={len(item.run_ids)}\n"
        )
    return True


async def _cmd_history(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    if not ctx.state.transcript:
        ctx.output("history> empty\n")
        return True
    for role, text in ctx.state.transcript[-30:]:
        compact = text.replace("\n", " ")
        if len(compact) > 80:
            compact = f"{compact[:80].rstrip()}..."
        ctx.output(f"{role}> {compact}\n")
    return True


async def _cmd_resume(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    if not args:
        ctx.output("chat> /resume requires session_id\n")
        return True
    record = ctx.session_store.get(args[0])
    if record is None:
        ctx.output(f"chat> unknown session '{args[0]}'\n")
        return True
    ctx.state.session_id = record.session_id
    ctx.state.thread_id = record.thread_id
    ctx.state.run_ids = list(record.run_ids)
    ctx.state.transcript = list(record.transcript)
    ctx.state.turn_index = len(ctx.state.run_ids)
    ctx.output(
        f"chat> resumed session={ctx.state.session_id} thread={ctx.state.thread_id}\n"
    )
    return True


async def _cmd_tools(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    if not ctx.selected_manifests:
        ctx.output("tools> none\n")
        return True
    verbose = bool(args and args[0].lower() == "verbose")
    if verbose:
        for manifest in ctx.selected_manifests:
            ctx.output(
                "tools> "
                f"{manifest.name} risk={manifest.risk.value} "
                f"side_effect={manifest.side_effect.value} "
                f"description={manifest.description}\n"
            )
        return True
    names = ", ".join(manifest.name for manifest in ctx.selected_manifests)
    ctx.output(f"tools> {names}\n")
    return True


async def _cmd_workspace(
    ctx: _ChatCommandContext, command: str, args: list[str]
) -> bool:
    if not args:
        ctx.output(f"workspace> {ctx.state.workspace_cwd or Path.cwd()}\n")
        return True
    try:
        ctx.state.workspace_cwd = _resolve_workspace_path(
            args[0], ctx.state.workspace_cwd
        )
    except ValueError as exc:
        ctx.output(f"workspace> error: {exc}\n")
        return True
    ctx.output(f"workspace> {ctx.state.workspace_cwd}\n")
    return True


async def _cmd_model(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    ctx.output(f"model> {ctx.model_name or 'default'}\n")
    return True


async def _cmd_provider(
    ctx: _ChatCommandContext, command: str, args: list[str]
) -> bool:
    ctx.output(f"provider> {ctx.provider_name}\n")
    return True


async def _cmd_limits(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    ctx.output(
        "limits> "
        f"max_steps={ctx.max_steps} max_tool_calls={ctx.max_tool_calls} "
        f"deadline_seconds={ctx.deadline_seconds}\n"
    )
    return True


async def _cmd_debug(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    if not args:
        ctx.output(f"debug> {'on' if ctx.state.debug_tool_protocol else 'off'}\n")
        return True
    value = args[0].lower()
    if value in {"on", "1", "true"}:
        ctx.state.debug_tool_protocol = True
        ctx.output("debug> on\n")
    elif value in {"off", "0", "false"}:
        ctx.state.debug_tool_protocol = False
        ctx.output("debug> off\n")
    else:
        ctx.output("chat> /debug expects on|off\n")
    return True


async def _cmd_doctor(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    python_settings = getattr(ctx.agent.runner.config, "python_tool", None)
    python_imports = (
        python_tool_runtime_facts(python_settings).imports_short
        if python_settings is not None and getattr(python_settings, "enabled", False)
        else "disabled"
    )
    tools = ", ".join(manifest.name for manifest in ctx.selected_manifests) or "none"
    last_signal = _doctor_last_signal(ctx.state, ctx.event_log)
    try:
        status = await ctx.agent.runner.deps.provider.healthcheck()
    except Exception as exc:  # noqa: BLE001 - doctor must not crash chat loop
        ctx.output(
            "doctor> "
            f"name={ctx.agent.runner.deps.provider.name} healthy=False configured=True "
            f"latency_ms=0 provider_check_error={_provider_check_error_label(exc)}\n"
        )
    else:
        ctx.output(
            "doctor> "
            f"name={status.provider_name} healthy={status.healthy} "
            f"configured={status.configured} latency_ms={status.latency_ms}\n"
        )
    ctx.output(
        f"doctor> limits max_steps={ctx.max_steps} max_tool_calls={ctx.max_tool_calls} "
        f"deadline_seconds={ctx.deadline_seconds}\n"
    )
    ctx.output(f"doctor> tools {tools}\n")
    ctx.output(f"doctor> python_imports {python_imports}\n")
    ctx.output(f"doctor> last_signal {last_signal}\n")
    return True


async def _cmd_save(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    record = ctx.session_store.upsert(
        session_id=ctx.state.session_id,
        thread_id=ctx.state.thread_id,
        run_ids=ctx.state.run_ids,
        transcript=ctx.state.transcript,
    )
    target = args[0] if args else str(ctx.session_store.path)
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
    ctx.output(f"save> {target}\n")
    return True


async def _cmd_export(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    export_path = Path(args[0]) if args else Path.cwd() / f"{ctx.state.session_id}.md"
    lines = [f"# Session {ctx.state.session_id}", ""]
    for role, text in ctx.state.transcript:
        lines.append(f"## {role}")
        lines.append(text)
        lines.append("")
    export_path.write_text("\n".join(lines), encoding="utf-8")
    ctx.output(f"export> {export_path}\n")
    return True


async def _cmd_resume_control(
    ctx: _ChatCommandContext, command: str, args: list[str]
) -> bool:
    if len(args) < 2:
        ctx.output(f"chat> /{command} requires <run_id> <interrupt_id>\n")
        return True
    run_id, interrupt_id = args[0], args[1]
    if command == "approve":
        _ = await ctx.agent.approve(run_id=run_id, interrupt_id=interrupt_id)
    elif command == "reject":
        message = " ".join(args[2:]) if len(args) >= 3 else None
        _ = await ctx.agent.reject(
            run_id=run_id, interrupt_id=interrupt_id, message=message
        )
    elif command == "cancel":
        _ = await ctx.agent.cancel(run_id=run_id, interrupt_id=interrupt_id)
    else:
        if len(args) < 3:
            ctx.output("chat> /clarify requires message after interrupt_id\n")
            return True
        _ = await ctx.agent.clarify(
            run_id=run_id, interrupt_id=interrupt_id, message=" ".join(args[2:])
        )
    ctx.output(f"resume> {command} ok run_id={run_id}\n")
    return True


async def _cmd_replay(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    run_id = _resolve_run_id(args, ctx.state)
    if run_id is None:
        ctx.output("chat> replay requires run_id or existing session run\n")
        return True
    for line in cli_replay_lines(ctx.event_log, run_id=run_id):
        ctx.output(f"{line}\n")
    return True


async def _cmd_tail(ctx: _ChatCommandContext, command: str, args: list[str]) -> bool:
    run_id = _resolve_run_id(args, ctx.state)
    if run_id is None:
        ctx.output("chat> tail requires run_id or existing session run\n")
        return True
    last_n = 20
    if len(args) >= 2:
        try:
            last_n = int(args[1])
        except ValueError:
            ctx.output("chat> tail last_n must be integer\n")
            return True
    for line in cli_tail_lines(ctx.event_log, run_id=run_id, last_n=last_n):
        ctx.output(f"{line}\n")
    return True


_CommandHandler = Callable[
    [_ChatCommandContext, str, list[str]], Awaitable[bool]
]

# One entry per slash command (aliases point at the same handler). The dispatch
# order no longer matters — each handler owns exactly its command.
_LOCAL_COMMAND_HANDLERS: dict[str, _CommandHandler] = {
    "help": _cmd_help,
    "exit": _cmd_exit,
    "quit": _cmd_exit,
    "clear": _cmd_clear,
    "reset": _cmd_reset,
    "plan": _cmd_plan,
    "runs": _cmd_runs,
    "sessions": _cmd_sessions,
    "history": _cmd_history,
    "resume": _cmd_resume,
    "tools": _cmd_tools,
    "workspace": _cmd_workspace,
    "cd": _cmd_workspace,
    "model": _cmd_model,
    "provider": _cmd_provider,
    "limits": _cmd_limits,
    "debug": _cmd_debug,
    "doctor": _cmd_doctor,
    "save": _cmd_save,
    "export": _cmd_export,
    "approve": _cmd_resume_control,
    "reject": _cmd_resume_control,
    "cancel": _cmd_resume_control,
    "clarify": _cmd_resume_control,
    "replay": _cmd_replay,
    "tail": _cmd_tail,
}


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
    renderer: object | None = None,
) -> bool:
    handler = _LOCAL_COMMAND_HANDLERS.get(command)
    if handler is None:
        output(f"chat> unknown command '/{command}'\n")
        return True
    ctx = _ChatCommandContext(
        agent=agent,
        state=state,
        event_log=event_log,
        session_store=session_store,
        provider_name=provider_name,
        model_name=model_name,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        deadline_seconds=deadline_seconds,
        selected_manifests=selected_manifests,
        output=output,
        clear_screen=clear_screen,
        welcome=welcome,
        renderer=renderer,
    )
    return await handler(ctx, command, args)


async def _run_shell_bang(
    command_text: str,
    output: Callable[[str], None],
    *,
    cwd: Path | None = None,
) -> None:
    output(f"● Bash(!{command_text})\n")
    process = await asyncio.create_subprocess_shell(
        command_text,
        cwd=str(cwd) if cwd is not None else None,
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
    workspace_cwd: str | Path | None = None,
    capability_pack_metadata: dict[str, object] | None = None,
    animate: bool = False,
    input_reader: Callable[[str], str] | None = None,
    output: Callable[[str], None] | None = None,
    tool_gate: ToolGate | None = None,
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
    initial_workspace = (
        _resolve_workspace_path(str(workspace_cwd), None) if workspace_cwd is not None else None
    )
    state = ChatSessionState(
        debug_tool_protocol=debug_tool_protocol,
        workspace_cwd=initial_workspace,
    )
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
                workspace_cwd=initial_workspace,
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
                cwd=str(state.workspace_cwd) if state.workspace_cwd is not None else None,
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
                cwd=str(state.workspace_cwd or Path.cwd()),
            git_branch=_detect_git_branch(),
            mode_label="chat+debug" if state.debug_tool_protocol else "chat",
        )

    def _clear_screen() -> None:
        renderer.emit_raw("\x1b[H\x1b[2J")
    _emit_welcome()
    if isinstance(state.planning_state, dict) and state.planning_state.get("todos"):
        renderer.refresh_plan_panel(state.planning_state)
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
    exit_code = 0
    try:
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
                    break
                except KeyboardInterrupt:
                    now = time.monotonic()
                    if now - last_keyboard_interrupt <= _CTRL_C_WINDOW_SECONDS:
                        renderer.emit_raw("\nchat> interrupted\n")
                        break
                    last_keyboard_interrupt = now
                    renderer.emit_raw("\nchat> press Ctrl+C again within 2s to exit\n")
                    continue
            text = raw.strip()
            if not text:
                continue
            if prompt_session is not None and renderer.rich_enabled:
                renderer.emit_raw(prompt_session.prompt_closing_frame())
            if text.startswith("!") and len(text) > 1:
                await _run_shell_bang(
                    text[1:].strip(),
                    renderer.emit_raw,
                    cwd=state.workspace_cwd,
                )
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
                    renderer=renderer,
                )
                if not keep_running:
                    break
                if prompt_session is not None and command in {"clear", "reset"}:
                    prompt_session.set_pressure(None)
                    prompt_session.set_budget_warning(None)
                    prompt_session.set_plan_progress(None, current=None)
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
            app_metadata: dict[str, object] = {
                "stream_poll_interval_ms": stream_poll_interval_ms,
                "chat_mode": True,
                "debug_tool_protocol": state.debug_tool_protocol,
            }
            app_metadata.update(dict(capability_pack_metadata or {}))
            if state.workspace_cwd is not None:
                app_metadata["workspace_cwd"] = str(state.workspace_cwd)
            if isinstance(state.planning_state, dict) and state.planning_state.get("todos"):
                app_metadata["planning_state_seed"] = state.planning_state
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
                    app_metadata=app_metadata,
                ),
                tool_gate=tool_gate,
            )
            try:
                (
                    assistant_text,
                    input_tokens,
                    output_tokens,
                    pressure_state,
                    planning_snapshot,
                ) = await render_chat_stream(
                    stream=stream,
                    output=renderer.emit_raw,
                    run_id=run_id,
                    renderer=renderer,
                    animate=effective_mode == "rich",
                )
            except RuntimeExecutionError as exc:
                renderer.emit_error_card(
                    title="Run failed",
                    reason=str(exc),
                    hint="Retry the prompt or reduce tool-heavy requests.",
                )
                continue
            session_input_tokens += input_tokens
            session_output_tokens += output_tokens
            if prompt_session is not None:
                prompt_session.set_usage(
                    input_tokens=session_input_tokens,
                    output_tokens=session_output_tokens,
                )
                prompt_session.set_pressure(pressure_state)
            if isinstance(planning_snapshot, dict) and planning_snapshot.get("todos"):
                state.planning_state = planning_snapshot
            if prompt_session is not None and isinstance(state.planning_state, dict):
                progress, current = plan_progress_footer(state.planning_state)
                prompt_session.set_plan_progress(
                    progress or None,
                    current=current,
                )
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
    finally:
        await _shutdown_chat_resources(agent, prompt_session=prompt_session)
    return exit_code


__all__ = [
    "ChatSessionState",
    "parse_chat_command",
    "render_chat_stream",
    "run_chat_session",
]
