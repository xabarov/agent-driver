"""App-facing SDK facade over low-level runtime runner."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
import uuid

from agent_driver.contracts.control import (
    ControlKind,
    ControlPriority,
    ControlRequest,
    ControlResponse,
)
from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.runner import SingleAgentRunner
from agent_driver.runtime.control import CommandQueueStore, InMemoryCommandQueueStore
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.runtime.stream import project_runtime_events


@dataclass(frozen=True, slots=True)
class AgentDefaults:
    """Default identifiers used by ergonomic helper methods."""

    agent_id: str = "agent"
    graph_preset: str = "single_react"


class Agent:
    """High-level facade for run/resume flows."""

    def __init__(
        self,
        runner: SingleAgentRunner,
        *,
        defaults: AgentDefaults | None = None,
        command_queue_store: CommandQueueStore | None = None,
    ) -> None:
        self._runner = runner
        self._defaults = defaults or AgentDefaults()
        self._command_queue_store = command_queue_store or InMemoryCommandQueueStore()

    @property
    def runner(self) -> SingleAgentRunner:
        """Expose low-level runner for advanced embedders."""
        return self._runner

    @property
    def command_queue_store(self) -> CommandQueueStore:
        """Expose steering command queue store for advanced embedders."""
        return self._command_queue_store

    def control(self, request: ControlRequest) -> ControlResponse:
        """Queue a typed steering control request."""
        item = self._command_queue_store.enqueue(request)
        return ControlResponse(
            ok=True,
            control_id=item.control_id,
            queue_id=item.queue_id,
        )

    def enqueue(
        self,
        message: str,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
        priority: ControlPriority = ControlPriority.NEXT,
        dedupe_key: str | None = None,
    ) -> ControlResponse:
        """Queue a user message for the next/later runtime boundary."""
        return self.control(
            ControlRequest(
                kind=ControlKind.ENQUEUE_USER_MESSAGE,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id or self._defaults.agent_id,
                priority=priority,
                payload={"message": message},
                dedupe_key=dedupe_key,
            )
        )

    def set_model(
        self,
        model: str,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> ControlResponse:
        """Queue a model change for the next runtime boundary."""
        return self.control(
            ControlRequest(
                kind=ControlKind.SET_MODEL,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id or self._defaults.agent_id,
                priority=ControlPriority.NEXT,
                payload={"model": model},
            )
        )

    def set_permission_mode(
        self,
        mode: str,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> ControlResponse:
        """Queue a permission-mode change for the next runtime boundary."""
        return self.control(
            ControlRequest(
                kind=ControlKind.SET_PERMISSION_MODE,
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id or self._defaults.agent_id,
                priority=ControlPriority.NEXT,
                payload={"mode": mode},
            )
        )

    def cancel_queued_message(self, queue_id: str) -> ControlResponse:
        """Cancel a pending queued steering command."""
        item = self._command_queue_store.cancel(queue_id)
        if item is None:
            return ControlResponse(ok=False, queue_id=queue_id, error="queue item not found")
        return ControlResponse(
            ok=True,
            control_id=item.control_id,
            queue_id=item.queue_id,
        )

    async def run(
        self,
        run_input: AgentRunInput,
        *,
        abort_handle: RunAbortHandle | None = None,
        tool_gate: ToolGate | None = None,
    ) -> AgentRunOutput:
        """Execute one agent run.

        ``abort_handle`` is an optional :class:`RunAbortHandle`. Flip
        it from any thread (``handle.abort(reason=...)``) to terminate
        the run at the next step boundary with
        ``RunStatus.CANCELLED`` / ``CANCELLED_BY_USER``. Subagents
        spawned during the run inherit a weakly-referenced child
        handle, so a single ``.abort()`` cascades through the tree.

        ``tool_gate`` is an optional :class:`ToolGate` (A0.2). When
        provided, every planned tool call is passed through the gate
        AFTER the static :class:`ToolPolicyInput` returns ALLOW; the
        gate can flip the decision to DENY (blocked envelope, LLM
        sees it and re-plans) or ASK (operator-facing
        :class:`InterruptRequest`). See
        :mod:`agent_driver.runtime.tool_gate` for the result contract
        and fail-closed semantics.
        """
        return await self._runner.run(
            run_input, abort_handle=abort_handle, tool_gate=tool_gate
        )

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
        """Yield normalized stream events incrementally during run execution."""
        effective_run_id = run_input.run_id or f"run_{uuid.uuid4().hex[:12]}"
        effective_input = (
            run_input
            if run_input.run_id
            else run_input.model_copy(update={"run_id": effective_run_id})
        )
        poll_interval_ms = int(
            effective_input.app_metadata.get("stream_poll_interval_ms", 20)
        )
        poll_seconds = max(0.01, poll_interval_ms / 1000.0)
        after_seq = 0
        run_task = asyncio.create_task(self.run(effective_input))
        try:
            while True:
                new_events = self._runner.deps.event_log.list_for_run(
                    effective_run_id, after_seq=after_seq
                )
                if new_events:
                    for event in project_runtime_events(new_events):
                        after_seq = event.seq
                        yield event
                    continue
                if run_task.done():
                    break
                await asyncio.sleep(poll_seconds)
            output = await run_task
            for event in project_runtime_events(output.events):
                if event.seq > after_seq:
                    after_seq = event.seq
                    yield event
        finally:
            if not run_task.done():
                run_task.cancel()
                with suppress(asyncio.CancelledError):
                    await run_task


__all__ = ["Agent", "AgentDefaults"]
