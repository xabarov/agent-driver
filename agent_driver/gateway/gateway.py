"""Headless, session-routed gateway over an agent-driver :class:`Agent`.

The gateway is the transport-neutral core a headless server (SSE/HTTP) or a
platform adapter sits on. It manages the part the raw SDK does not: routing
turns by ``session_id`` and correlating the **approval round-trip** — when a
run pauses on an ``action_required`` interrupt, the gateway parks it and emits
an event; the client later calls :meth:`respond` to resume it.

It is dependency-free and transport-agnostic: :meth:`submit` / :meth:`respond`
yield :class:`GatewayEvent` objects; a transport renders them (e.g.
``event.to_sse()``). Live token streaming is intentionally out of this slice —
the existing :mod:`agent_driver.adapters.sse` covers pure streaming; this core
owns the session + approval lifecycle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agent_driver.contracts.enums import ResumeAction, RunStatus
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.gateway.events import GatewayEvent, GatewayEventKind

if TYPE_CHECKING:
    from agent_driver.runtime.tool_gate import ToolGate
    from agent_driver.sdk.agent import Agent


class GatewayError(RuntimeError):
    """Raised on an invalid gateway operation (e.g. respond with no pending)."""


@dataclass(frozen=True, slots=True)
class _Parked:
    """A run paused awaiting an operator decision."""

    run_id: str
    interrupt_id: str


class AgentGateway:
    """Session-routed run/approve lifecycle over an :class:`Agent`."""

    def __init__(
        self,
        agent: "Agent",
        *,
        tool_gate: "ToolGate | None" = None,
    ) -> None:
        self._agent = agent
        self._tool_gate = tool_gate
        self._parked: dict[tuple[str, str], _Parked] = {}

    async def submit(
        self, session_id: str, text: str, *, run_id: str | None = None
    ) -> AsyncIterator[GatewayEvent]:
        """Start a turn for ``session_id`` and stream its lifecycle events."""
        resolved = run_id or f"run_{uuid4().hex[:12]}"
        run_input = AgentRunInput(
            input=text,
            run_id=resolved,
            thread_id=session_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
        )
        seq = _SeqCounter()
        yield self._event(GatewayEventKind.STARTED, session_id, resolved, seq, {})
        output = await self._agent.run(run_input, tool_gate=self._tool_gate)
        for event in self._terminal_events(session_id, output, seq):
            yield event

    async def respond(
        self,
        session_id: str,
        *,
        run_id: str,
        action: ResumeAction,
        interrupt_id: str | None = None,
        message: str | None = None,
        edited_tool_args: dict[str, Any] | None = None,
    ) -> AsyncIterator[GatewayEvent]:
        """Resume a parked run with an operator decision and stream the rest."""
        key = (session_id, run_id)
        parked = self._parked.get(key)
        if parked is None:
            raise GatewayError(
                f"no pending approval for run {run_id!r} in session {session_id!r}"
            )
        resolved_interrupt = interrupt_id or parked.interrupt_id
        del self._parked[key]
        seq = _SeqCounter()
        output = await self._agent.resume(
            run_id=run_id,
            interrupt_id=resolved_interrupt,
            action=action,
            message=message,
            edited_tool_args=edited_tool_args,
        )
        for event in self._terminal_events(session_id, output, seq):
            yield event

    def pending(self, session_id: str) -> list[str]:
        """Return run ids parked awaiting a decision in this session."""
        return [run_id for (sid, run_id) in self._parked if sid == session_id]

    def _terminal_events(
        self, session_id: str, output: AgentRunOutput, seq: "_SeqCounter"
    ) -> list[GatewayEvent]:
        if output.status == RunStatus.PAUSED and output.interrupt is not None:
            interrupt = output.interrupt
            self._parked[(session_id, output.run_id)] = _Parked(
                run_id=output.run_id, interrupt_id=interrupt.interrupt_id
            )
            return [
                self._event(
                    GatewayEventKind.ACTION_REQUIRED,
                    session_id,
                    output.run_id,
                    seq,
                    {
                        "interrupt_id": interrupt.interrupt_id,
                        "reason": interrupt.reason.value,
                        "title": interrupt.title,
                        "description": interrupt.description,
                        "allowed_actions": [a.value for a in interrupt.allowed_actions],
                        "proposed_action": interrupt.proposed_action,
                    },
                )
            ]
        if output.status == RunStatus.COMPLETED:
            return [
                self._event(
                    GatewayEventKind.COMPLETED,
                    session_id,
                    output.run_id,
                    seq,
                    {"answer": output.answer or ""},
                )
            ]
        reason = getattr(output.terminal_reason, "value", None)
        return [
            self._event(
                GatewayEventKind.FAILED,
                session_id,
                output.run_id,
                seq,
                {"status": output.status.value, "reason": reason},
            )
        ]

    @staticmethod
    def _event(
        kind: GatewayEventKind,
        session_id: str,
        run_id: str,
        seq: "_SeqCounter",
        data: dict[str, Any],
    ) -> GatewayEvent:
        return GatewayEvent(
            kind=kind,
            session_id=session_id,
            run_id=run_id,
            seq=seq.next(),
            data=data,
        )


class _SeqCounter:
    """Monotonic per-stream sequence for resumable SSE ids."""

    def __init__(self) -> None:
        self._value = -1

    def next(self) -> int:
        """Return the next monotonic sequence value (0-based)."""
        self._value += 1
        return self._value


__all__ = ["AgentGateway", "GatewayError"]
