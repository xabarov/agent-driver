"""Session facade for SDK chat-style integrations."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING
from uuid import uuid4

from agent_driver.contracts.context import SessionTurn
from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.handle import RunHandle, RunStream

if TYPE_CHECKING:
    from agent_driver.sdk.subagent import SubagentResult, SubagentSpec


class Session:
    """Thread-scoped SDK facade over an :class:`Agent`."""

    def __init__(self, agent: object, session_id: str) -> None:
        self._agent = agent
        self.session_id = session_id

    async def send(
        self,
        text: str,
        *,
        run_id: str | None = None,
        app_metadata: dict[str, object] | None = None,
        tool_gate: ToolGate | None = None,
        reasoning_effort: str | None = None,
        model_role: str | None = None,
    ) -> AgentRunOutput:
        """Send one user turn and await the final output.

        ``reasoning_effort`` (R1) / ``model_role`` (R2/R3) tag this turn for
        thinking-tier and role→model / difficulty routing; ``None`` leaves the
        provider default / ``"default"`` role.
        """
        return await self._agent.run(
            self._run_input(
                text,
                run_id=run_id,
                stream=False,
                app_metadata=app_metadata,
                reasoning_effort=reasoning_effort,
                model_role=model_role,
            ),
            tool_gate=tool_gate,
        )

    def stream(
        self,
        text: str,
        *,
        run_id: str | None = None,
        app_metadata: dict[str, object] | None = None,
        tool_gate: ToolGate | None = None,
        reasoning_effort: str | None = None,
        model_role: str | None = None,
    ) -> RunStream:
        """Start one streamed user turn and return a stream helper.

        ``reasoning_effort`` (R1) / ``model_role`` (R2/R3) tag this turn (same as
        :meth:`send`).
        """
        return self._agent.stream_run(
            self._run_input(
                text,
                run_id=run_id,
                stream=True,
                app_metadata=app_metadata,
                reasoning_effort=reasoning_effort,
                model_role=model_role,
            ),
            tool_gate=tool_gate,
        )

    async def resume(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        action: ResumeAction,
        edited_tool_args: dict[str, object] | None = None,
        message: str | None = None,
    ) -> AgentRunOutput:
        """Resume an interrupted run within this session."""
        return await self._agent.run(
            AgentRunInput(
                run_id=run_id,
                thread_id=self.session_id,
                agent_id=self._agent.defaults.agent_id,
                graph_preset=self._agent.defaults.graph_preset,
                resume=ResumeCommand(
                    interrupt_id=interrupt_id,
                    action=action,
                    edited_tool_args=edited_tool_args,
                    message=message,
                ),
            )
        )

    def history(self) -> list[SessionTurn]:
        """Return persisted turns for this session."""
        return self._agent.runner.deps.session_store.list_turns(self.session_id)

    def runs(self) -> list[str]:
        """Return known run ids for this session from persisted turns."""
        seen: set[str] = set()
        ordered: list[str] = []
        for turn in self.history():
            run_id = turn.metadata.get("run_id")
            if isinstance(run_id, str) and run_id and run_id not in seen:
                seen.add(run_id)
                ordered.append(run_id)
        return ordered

    def start(
        self,
        text: str,
        *,
        run_id: str | None = None,
        app_metadata: dict[str, object] | None = None,
        tool_gate: ToolGate | None = None,
        reasoning_effort: str | None = None,
        model_role: str | None = None,
    ) -> RunHandle:
        """Start one user turn in the background and return a handle.

        ``reasoning_effort`` (R1) / ``model_role`` (R2/R3) tag this turn (same as
        :meth:`send`).
        """
        return self._agent.start(
            self._run_input(
                text,
                run_id=run_id,
                stream=False,
                app_metadata=app_metadata,
                reasoning_effort=reasoning_effort,
                model_role=model_role,
            ),
            tool_gate=tool_gate,
        )

    async def fork(
        self,
        parent_system_prompt: str,
        spec: SubagentSpec,
        *,
        parent_run_id: str | None = None,
        tool_gate: ToolGate | None = None,
    ) -> SubagentResult:
        """Fork a subagent using the shared SDK fork helper."""
        fork_module = import_module("agent_driver.sdk.fork")

        return await fork_module.fork_subagent(
            self._agent,
            parent_system_prompt,
            spec,
            parent_run_id=parent_run_id,
            tool_gate=tool_gate,
        )

    def _run_input(
        self,
        text: str,
        *,
        run_id: str | None,
        stream: bool,
        app_metadata: dict[str, object] | None,
        reasoning_effort: str | None = None,
        model_role: str | None = None,
    ) -> AgentRunInput:
        # ``model_role`` is a non-optional field (default ``"default"``); only
        # override it when the caller actually asked, so None is a true no-op.
        extra: dict[str, object] = {}
        if model_role is not None:
            extra["model_role"] = model_role
        return AgentRunInput(
            input=text,
            run_id=run_id or f"run_{uuid4().hex[:12]}",
            thread_id=self.session_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            stream=stream,
            app_metadata=app_metadata or {},
            reasoning_effort=reasoning_effort,
            **extra,
        )


__all__ = ["Session"]
