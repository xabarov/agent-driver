"""Interactive terminal chat loop for agent-driver CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import uuid

from agent_driver.adapters import cli_replay_lines, cli_tail_lines
from agent_driver.contracts import ToolManifest
from agent_driver.contracts import AgentRunInput, RunStreamEvent
from agent_driver.cli.prompt_icon import PromptSpinner
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.sdk import Agent
from agent_driver.cli.sessions import SessionStore

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


def _format_compact_event(event: RunStreamEvent) -> str | None:
    name = event.event
    if name in {
        "run_started",
        "llm_call_started",
        "llm_call_completed",
        "checkpoint_saved",
        "node_started",
        "node_completed",
        "guardrail_decision",
    }:
        return None
    if name in _TERMINAL_EVENTS:
        reason = event.data.get("reason")
        return f"run {name}" if reason is None else f"run {name} reason={reason}"
    if name in {"tool_call_started", "tool_call_completed"}:
        tools = event.data.get("tools")
        if isinstance(tools, list) and tools:
            rendered: list[str] = []
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                tool_name = str(tool.get("tool_name") or "?")
                status = tool.get("status")
                summary = tool.get("result_summary")
                suffix = f" status={status}" if isinstance(status, str) and status else ""
                if isinstance(summary, str) and summary.strip():
                    suffix = f"{suffix} summary={summary.strip()[:80]}"
                rendered.append(f"{tool_name}{suffix}")
            if rendered:
                phase = "start" if name == "tool_call_started" else "done"
                return f"tool {phase} " + " | ".join(rendered)
        tool_name = event.data.get("tool_name", "?")
        status = event.data.get("status")
        suffix = f" status={status}" if status else ""
        return f"tool {name} tool={tool_name}{suffix}"
    if name == "warning":
        kind = str(event.data.get("kind", "warning"))
        if kind == "tool_protocol_debug":
            return (
                "warning kind=tool_protocol_debug "
                f"messages={event.data.get('message_count')} "
                f"roles={event.data.get('roles')} "
                f"tool_choice={event.data.get('tool_choice')} "
                f"tool_names={event.data.get('tool_names')}"
            )
        return f"warning kind={kind}"
    if name in {"interrupt_requested", "run_paused"}:
        reason = event.data.get("reason", "unknown")
        return f"interrupt reason={reason}"
    return f"event {name}"


async def render_chat_stream(
    *,
    stream,
    output: Callable[[str], None],
    run_id: str,
    animate: bool = False,
) -> str:
    """Render stream to chat-oriented output and return assistant text."""
    assistant_parts: list[str] = []
    token_line_open = False
    saw_terminal = False
    tools_used = 0
    warnings_seen = 0
    spinner = PromptSpinner(output=output, enabled=animate)
    spinner.start()
    try:
        async for event in stream:
            if event.event == "token_delta":
                await spinner.stop()
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
            await spinner.stop()
            if token_line_open:
                output("\n")
                token_line_open = False
            if compact.startswith("tool "):
                tools_used += 1
                output(f"tool> {compact}\n")
            elif compact.startswith("warning "):
                warnings_seen += 1
                output(f"warn> {compact}\n")
            else:
                output(f"event> {compact}\n")
                if event.event == "run_failed":
                    reason = str(event.data.get("reason") or "")
                    if reason == "max_steps_exceeded":
                        output(
                            "hint> run failed by max steps limit; increase --max-steps if needed\n"
                        )
                    elif reason == "tool_policy_denied":
                        output(
                            "hint> run stopped by tool-call budget/policy; check --max-tool-calls and tool policy\n"
                        )
            if event.event in _TERMINAL_EVENTS:
                saw_terminal = True
            elif not token_line_open:
                spinner.start()
    finally:
        await spinner.stop(clear=not token_line_open)
    if token_line_open:
        output("\n")
    assistant_text = "".join(assistant_parts)
    if not assistant_text and not saw_terminal:
        output("assistant> [no textual response]\n")
    output(f"run> {run_id} tools_used={tools_used} warnings={warnings_seen}\n")
    return assistant_text


def _print_help(output: Callable[[str], None]) -> None:
    output(
        "Commands: /help /exit /quit /clear /runs /sessions /history "
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
            output(f"{role}> {text}\n")
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
        output(
            "doctor> "
            f"name={status.provider_name} healthy={status.healthy} "
            f"configured={status.configured} latency_ms={status.latency_ms}\n"
        )
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
    animate: bool = False,
    input_reader: Callable[[str], str] | None = None,
    output: Callable[[str], None] | None = None,
) -> int:
    """Run interactive chat loop until explicit exit or EOF."""
    read = input_reader or input
    write = output or (lambda text: print(text, end="", flush=True))
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
        assistant_text = await render_chat_stream(
            stream=stream,
            output=write,
            run_id=run_id,
            animate=animate,
        )
        if assistant_text:
            state.transcript.append(("assistant", assistant_text))
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
