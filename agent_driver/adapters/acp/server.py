"""ACP agent-side server: bridges an :class:`Agent` to the Agent Client Protocol.

Implements the ACP ``Agent`` protocol (``initialize``/``authenticate``/
``new_session``/``prompt``/``cancel``). A prompt streams token/reasoning deltas
live from the runtime's event stream, reconstructs the tool-call timeline from
each finished leg's trace, and bridges runtime approval interrupts to the ACP
``request_permission`` round-trip (reusing the same resume semantics the
in-process gateway uses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import acp

from agent_driver.adapters.acp.mapping import (
    permission_options_for,
    permission_tool_call,
    resume_action_from_outcome,
    stop_reason_for,
    tool_updates_from_trace,
)
from agent_driver.adapters.acp.session import AcpSession
from agent_driver.contracts.enums import ResumeAction, RunStatus
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.runtime.abort import RunAbortHandle

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
        return acp.InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_info=acp.schema.Implementation(
                name=self._name, version=self._version
            ),
            agent_capabilities=acp.schema.AgentCapabilities(
                load_session=False,
                prompt_capabilities=acp.schema.PromptCapabilities(
                    image=False, audio=False
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
        return acp.NewSessionResponse(session_id=session_id)

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

        run_input = AgentRunInput(
            input=_prompt_text(prompt),
            run_id=f"run_{uuid4().hex[:12]}",
            thread_id=session.thread_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            app_metadata={"workspace_cwd": session.cwd} if session.cwd else {},
        )

        emitted_tools: set[str] = set()
        try:
            output = await self._agent.run(run_input, abort_handle=abort)
            await self._emit_leg(session_id, output, emitted_tools)
            output = await self._drive_resume_loop(
                session_id, output, emitted_tools, abort
            )
        finally:
            self._aborts.pop(session_id, None)

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
        """Emit a finished leg's tool timeline, then its answer text."""
        await self._emit_tools(session_id, output, emitted_tools)
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

    async def _emit_tools(
        self, session_id: str, output: AgentRunOutput, emitted: set[str]
    ) -> None:
        for update in tool_updates_from_trace(output, emitted=emitted):
            await self._send(session_id, update)

    async def _send(self, session_id: str, update: Any) -> None:
        if self._conn is not None:
            await self._conn.session_update(session_id=session_id, update=update)
