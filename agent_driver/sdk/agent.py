"""App-facing SDK facade over low-level runtime runner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.runner import SingleAgentRunner
from agent_driver.runtime.stream import project_runtime_events


@dataclass(frozen=True, slots=True)
class AgentDefaults:
    """Default identifiers used by ergonomic helper methods."""

    agent_id: str = "agent"
    graph_preset: str = "single_react"


class Agent:
    """High-level facade for run/resume flows."""

    def __init__(
        self, runner: SingleAgentRunner, *, defaults: AgentDefaults | None = None
    ) -> None:
        self._runner = runner
        self._defaults = defaults or AgentDefaults()

    @property
    def runner(self) -> SingleAgentRunner:
        """Expose low-level runner for advanced embedders."""
        return self._runner

    async def run(self, run_input: AgentRunInput) -> AgentRunOutput:
        """Execute one agent run."""
        return await self._runner.run(run_input)

    async def run_text(
        self,
        text: str,
        *,
        run_id: str | None = None,
        stream: bool = False,
        app_metadata: dict[str, object] | None = None,
    ) -> AgentRunOutput:
        """Execute one run from plain user text with SDK defaults."""
        return await self.run(
            AgentRunInput(
                input=text,
                run_id=run_id,
                agent_id=self._defaults.agent_id,
                graph_preset=self._defaults.graph_preset,
                stream=stream,
                app_metadata=app_metadata or {},
            )
        )

    async def resume(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        action: ResumeAction,
        agent_id: str | None = None,
        graph_preset: str | None = None,
        edited_tool_args: dict[str, object] | None = None,
        message: str | None = None,
    ) -> AgentRunOutput:
        """Resume an interrupted run via normalized resume command."""
        return await self.run(
            AgentRunInput(
                run_id=run_id,
                agent_id=agent_id or self._defaults.agent_id,
                graph_preset=graph_preset or self._defaults.graph_preset,
                resume=ResumeCommand(
                    interrupt_id=interrupt_id,
                    action=action,
                    edited_tool_args=edited_tool_args,
                    message=message,
                ),
            )
        )

    async def approve(self, *, run_id: str, interrupt_id: str) -> AgentRunOutput:
        """Resume with approve action."""
        return await self.resume(
            run_id=run_id,
            interrupt_id=interrupt_id,
            action=ResumeAction.APPROVE,
        )

    async def reject(
        self, *, run_id: str, interrupt_id: str, message: str | None = None
    ) -> AgentRunOutput:
        """Resume with reject action."""
        return await self.resume(
            run_id=run_id,
            interrupt_id=interrupt_id,
            action=ResumeAction.REJECT,
            message=message,
        )

    async def edit(
        self, *, run_id: str, interrupt_id: str, edited_tool_args: dict[str, object]
    ) -> AgentRunOutput:
        """Resume with edited tool arguments."""
        return await self.resume(
            run_id=run_id,
            interrupt_id=interrupt_id,
            action=ResumeAction.EDIT,
            edited_tool_args=edited_tool_args,
        )

    async def cancel(self, *, run_id: str, interrupt_id: str) -> AgentRunOutput:
        """Resume with cancel action."""
        return await self.resume(
            run_id=run_id,
            interrupt_id=interrupt_id,
            action=ResumeAction.CANCEL,
        )

    async def clarify(
        self, *, run_id: str, interrupt_id: str, message: str
    ) -> AgentRunOutput:
        """Resume with clarification message."""
        return await self.resume(
            run_id=run_id,
            interrupt_id=interrupt_id,
            action=ResumeAction.CLARIFY,
            message=message,
        )

    async def stream(self, run_input: AgentRunInput) -> AsyncIterator[RunStreamEvent]:
        """Yield normalized stream events from one run execution."""
        output = await self.run(run_input)
        for event in project_runtime_events(output.events):
            yield event


__all__ = ["Agent", "AgentDefaults"]
