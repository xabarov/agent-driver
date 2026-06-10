"""ACP agent-side server: bridges an :class:`Agent` to the Agent Client Protocol.

Implements the ACP ``Agent`` protocol: the core
``initialize``/``authenticate``/``new_session``/``prompt``/``cancel`` plus the
session-richness methods ``load_session``/``resume_session``/``set_session_mode``.
A prompt emits each finished leg's answer and tool-call timeline (reconstructed
from its trace) and bridges runtime approval interrupts to the ACP
``request_permission`` round-trip (reusing the same resume semantics the
in-process gateway uses). ``set_session_mode`` maps an ACP session mode to a
per-run permission gate; ``load_session`` replays the recorded transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import acp

from agent_driver.adapters.acp.fs import AcpClientFileIO, client_fs_flags
from agent_driver.adapters.acp.mapping import (
    available_commands_update,
    current_mode_update,
    gate_for_mode,
    history_updates,
    is_known_mode,
    permission_options_for,
    permission_tool_call,
    plan_update_from_results,
    resume_action_from_outcome,
    session_mode_state,
    slash_command_name,
    slash_help_text,
    stop_reason_for,
    tool_updates_from_trace,
)
from agent_driver.adapters.acp.session import AcpSession
from agent_driver.adapters.acp.terminal import (
    AcpTerminalRunner,
    client_terminal_enabled,
)
from agent_driver.contracts.enums import ResumeAction, RunStatus
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.tools.context import command_runner_scope, fs_io_scope

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent


def _prompt_text(blocks: list[Any]) -> str:
    """Concatenate the text content blocks of an ACP prompt."""
    parts: list[str] = []
    for block in blocks or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts).strip()


class AgentAcpServer:
    """Expose an :class:`Agent` over ACP. Satisfies the ``acp.Agent`` protocol."""

    def __init__(
        self,
        agent: "Agent",
        *,
        name: str = "agent-driver",
        version: str = "0.1.0",
    ) -> None:
        self._agent = agent
        self._name = name
        self._version = version
        self._conn: acp.Client | None = None
        self._sessions: dict[str, AcpSession] = {}
        self._cancelled: set[str] = set()
        self._aborts: dict[str, RunAbortHandle] = {}
        # Client filesystem capability captured at initialize; gates whether
        # file tools route through the editor (fs/read_text_file/write_text_file).
        self._client_fs_read = False
        self._client_fs_write = False
        self._client_terminal = False

    # -- connection / capabilities ----------------------------------------

    def on_connect(self, conn: acp.Client) -> None:
        """Store the client connection used to push session updates."""
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Any | None = None,
        **_: Any,
    ) -> acp.InitializeResponse:
        """Advertise agent identity and capabilities."""
        self._client_fs_read, self._client_fs_write = client_fs_flags(
            client_capabilities
        )
        self._client_terminal = client_terminal_enabled(client_capabilities)
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_info=acp.schema.Implementation(
                name=self._name, version=self._version
            ),
            agent_capabilities=acp.schema.AgentCapabilities(
                load_session=True,
                prompt_capabilities=acp.schema.PromptCapabilities(
                    image=False, audio=False
                ),
                session_capabilities=acp.schema.SessionCapabilities(
                    resume=acp.schema.SessionResumeCapabilities(),
                    list=acp.schema.SessionListCapabilities(),
                    fork=acp.schema.SessionForkCapabilities(),
                    close=acp.schema.SessionCloseCapabilities(),
                ),
            ),
            auth_methods=[],
        )

    async def authenticate(self, method_id: str, **_: Any) -> acp.AuthenticateResponse:
        """No authentication for the stdio transport."""
        return acp.AuthenticateResponse()

    # -- sessions ----------------------------------------------------------

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **_: Any,
    ) -> acp.NewSessionResponse:
        """Allocate a session bound to a fresh runtime thread."""
        session_id = f"acp_{uuid4().hex[:12]}"
        self._sessions[session_id] = AcpSession(
            session_id=session_id, thread_id=session_id, cwd=cwd
        )
        await self._send(session_id, available_commands_update())
        return acp.NewSessionResponse(session_id=session_id, modes=session_mode_state())

    async def load_session(
        self,
        session_id: str,
        cwd: str | None = None,
        mcp_servers: list[Any] | None = None,
        **_: Any,
    ) -> acp.schema.LoadSessionResponse:
        """Restore a session and replay its persisted history to the client.

        Per the ACP contract the conversation so far is streamed via
        ``session_update`` notifications *before* this returns.
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = AcpSession(session_id=session_id, thread_id=session_id, cwd=cwd)
            self._sessions[session_id] = session
        elif cwd is not None:
            session.cwd = cwd
        await self._replay_history(session_id)
        await self._send(session_id, available_commands_update())
        return acp.schema.LoadSessionResponse(modes=session_mode_state(session.mode_id))

    async def resume_session(
        self,
        session_id: str,
        cwd: str | None = None,
        mcp_servers: list[Any] | None = None,
        **_: Any,
    ) -> acp.schema.ResumeSessionResponse:
        """Continue an existing session without replaying its history."""
        session = self._sessions.get(session_id)
        if session is None:
            session = AcpSession(session_id=session_id, thread_id=session_id, cwd=cwd)
            self._sessions[session_id] = session
        elif cwd is not None:
            session.cwd = cwd
        return acp.schema.ResumeSessionResponse(
            modes=session_mode_state(session.mode_id)
        )

    async def set_session_mode(
        self, session_id: str, mode_id: str, **_: Any
    ) -> acp.schema.SetSessionModeResponse:
        """Switch a session's permission mode (maps to a per-run tool gate)."""
        session = self._sessions.get(session_id) or AcpSession(
            session_id=session_id, thread_id=session_id
        )
        self._sessions[session_id] = session
        if is_known_mode(mode_id):
            session.mode_id = mode_id
            session.gate_override = gate_for_mode(mode_id)
            await self._send(session_id, current_mode_update(mode_id))
        return acp.schema.SetSessionModeResponse()

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **_: Any
    ) -> acp.schema.ListSessionsResponse:
        """List the adapter's known sessions (no pagination — returns all)."""
        sessions = [
            acp.schema.SessionInfo(session_id=sid, cwd=session.cwd or "")
            for sid, session in self._sessions.items()
        ]
        return acp.schema.ListSessionsResponse(sessions=sessions, next_cursor=None)

    async def fork_session(
        self,
        session_id: str,
        cwd: str | None = None,
        mcp_servers: list[Any] | None = None,
        **_: Any,
    ) -> acp.schema.ForkSessionResponse:
        """Branch a session into a new one, copying its transcript and mode."""
        parent = self._sessions.get(session_id)
        new_id = f"acp_{uuid4().hex[:12]}"
        forked = AcpSession(
            session_id=new_id,
            thread_id=new_id,
            cwd=cwd if cwd is not None else (parent.cwd if parent else None),
        )
        if parent is not None:
            forked.transcript = list(parent.transcript)
            forked.mode_id = parent.mode_id
            forked.gate_override = parent.gate_override
        self._sessions[new_id] = forked
        return acp.schema.ForkSessionResponse(
            session_id=new_id, modes=session_mode_state(forked.mode_id)
        )

    async def close_session(
        self, session_id: str, **_: Any
    ) -> acp.schema.CloseSessionResponse:
        """Discard a session's adapter-side state."""
        self._sessions.pop(session_id, None)
        self._cancelled.discard(session_id)
        self._aborts.pop(session_id, None)
        return acp.schema.CloseSessionResponse()

    async def cancel(self, session_id: str, **_: Any) -> None:
        """Cancel the active prompt for this session.

        Flags the session and aborts the in-flight run (if any) at the next
        step boundary.
        """
        self._cancelled.add(session_id)
        abort = self._aborts.get(session_id)
        if abort is not None:
            abort.abort(reason="acp_cancel")

    # -- prompt ------------------------------------------------------------

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **_: Any,
    ) -> acp.PromptResponse:
        """Run one turn, streaming updates and bridging approval interrupts."""
        session = self._sessions.get(session_id) or AcpSession(
            session_id=session_id, thread_id=session_id
        )
        self._sessions[session_id] = session
        self._cancelled.discard(session_id)
        abort = RunAbortHandle()
        self._aborts[session_id] = abort

        user_text = _prompt_text(prompt)
        if user_text:
            session.transcript.append(ChatMessage(role="user", content=user_text))

        command = slash_command_name(user_text)
        if command is not None:
            self._aborts.pop(session_id, None)
            return await self._handle_slash_command(session_id, session, command)

        run_input = AgentRunInput(
            input=user_text,
            run_id=f"run_{uuid4().hex[:12]}",
            thread_id=session.thread_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            app_metadata={"workspace_cwd": session.cwd} if session.cwd else {},
        )

        emitted_tools: set[str] = set()
        try:
            with (
                fs_io_scope(self._file_io(session)),
                command_runner_scope(self._command_runner(session)),
            ):
                output = await self._agent.run(
                    run_input, abort_handle=abort, tool_gate=session.gate_override
                )
                await self._emit_leg(session_id, output, emitted_tools)
                output = await self._drive_resume_loop(
                    session_id, output, emitted_tools, abort
                )
        finally:
            self._aborts.pop(session_id, None)

        if output.status == RunStatus.COMPLETED and output.answer:
            session.transcript.append(
                ChatMessage(role="assistant", content=output.answer)
            )

        if session_id in self._cancelled or output.status == RunStatus.CANCELLED:
            return acp.PromptResponse(stop_reason="cancelled")
        return acp.PromptResponse(stop_reason=stop_reason_for(output))

    async def _drive_resume_loop(
        self,
        session_id: str,
        output: AgentRunOutput,
        emitted_tools: set[str],
        abort: RunAbortHandle,
    ) -> AgentRunOutput:
        """Resume across approval interrupts until the run reaches a terminal."""
        while (
            output.status == RunStatus.PAUSED
            and output.interrupt is not None
            and session_id not in self._cancelled
        ):
            action = await self._request_permission(session_id, output.interrupt)
            if session_id in self._cancelled:
                break
            output = await self._agent.resume(
                run_id=output.run_id,
                interrupt_id=output.interrupt.interrupt_id,
                action=action,
            )
            await self._emit_leg(session_id, output, emitted_tools)
        return output

    async def _emit_leg(
        self, session_id: str, output: AgentRunOutput, emitted_tools: set[str]
    ) -> None:
        """Emit a finished leg's tool timeline, plan update, then answer text."""
        await self._emit_tools(session_id, output, emitted_tools)
        plan = plan_update_from_results(output)
        if plan is not None:
            await self._send(session_id, plan)
        if output.status == RunStatus.COMPLETED and output.answer:
            await self._send(session_id, acp.update_agent_message_text(output.answer))

    # -- helpers -----------------------------------------------------------

    async def _request_permission(
        self, session_id: str, interrupt: InterruptRequest
    ) -> ResumeAction:
        if self._conn is None:
            return ResumeAction.REJECT
        response = await self._conn.request_permission(
            options=permission_options_for(interrupt),
            session_id=session_id,
            tool_call=permission_tool_call(interrupt),
        )
        return resume_action_from_outcome(response.outcome)

    def _file_io(self, session: AcpSession) -> AcpClientFileIO | None:
        """Route file tools through the editor when the client advertised fs."""
        if self._conn is None or not (self._client_fs_read or self._client_fs_write):
            return None
        return AcpClientFileIO(
            self._conn,
            session.session_id,
            can_read=self._client_fs_read,
            can_write=self._client_fs_write,
        )

    def _command_runner(self, session: AcpSession) -> AcpTerminalRunner | None:
        """Route shell commands to the editor terminal when client supports it."""
        if self._conn is None or not self._client_terminal:
            return None
        return AcpTerminalRunner(self._conn, session.session_id)

    async def _handle_slash_command(
        self, session_id: str, session: AcpSession, command: str
    ) -> acp.PromptResponse:
        """Handle an in-band slash command without invoking the model."""
        if command == "clear":
            session.transcript.clear()
            await self._send(
                session_id, acp.update_agent_message_text("Conversation cleared.")
            )
        elif command == "help":
            await self._send(
                session_id, acp.update_agent_message_text(slash_help_text())
            )
        return acp.PromptResponse(stop_reason="end_turn")

    async def _replay_history(self, session_id: str) -> None:
        """Stream the session's recorded transcript to the client."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        for update in history_updates(session.transcript):
            await self._send(session_id, update)

    async def _emit_tools(
        self, session_id: str, output: AgentRunOutput, emitted: set[str]
    ) -> None:
        for update in tool_updates_from_trace(output, emitted=emitted):
            await self._send(session_id, update)

    async def _send(self, session_id: str, update: Any) -> None:
        if self._conn is not None:
            await self._conn.session_update(session_id=session_id, update=update)
