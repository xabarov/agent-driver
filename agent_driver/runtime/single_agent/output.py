"""Build AgentRunOutput for terminal and paused states."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import RunStatus
from agent_driver.contracts.interrupts import ApprovalPayload, InterruptRequest
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.contracts.tools import ToolTrace
from agent_driver.runtime.single_agent.types import (
    RunContext,
    RunnerDeps,
    TerminalResult,
)


class SingleAgentOutputMixin:  # pylint: disable=too-few-public-methods
    """Mixin: normalized run output envelopes."""

    _deps: RunnerDeps

    def _build_output(
        self,
        context: RunContext,
        terminal: TerminalResult,
    ) -> AgentRunOutput:
        answer = context.llm_response.message.content if context.llm_response else None
        usage = context.llm_response.usage if context.llm_response else None
        messages = [ChatMessage(role="assistant", content=answer)] if answer else []
        tool_trace_payload = context.metadata.get("tool_trace", [])
        tool_trace = []
        if isinstance(tool_trace_payload, list):
            tool_trace = [
                ToolTrace.model_validate(item)
                for item in tool_trace_payload
                if isinstance(item, dict)
            ]
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=terminal.status,
            answer=answer,
            messages=messages,
            events=self._deps.event_log.list_for_run(context.run_id),
            tool_trace=tool_trace,
            usage=usage,
            interrupt=context.metadata.get("interrupt_payload"),
            terminal_reason=terminal.reason,
            metadata={
                "graph_id": self.graph_id,
                "tool_results": context.metadata.get("tool_results", []),
                "approval_payload": (
                    ApprovalPayload.from_interrupt(
                        InterruptRequest.model_validate(
                            context.metadata["interrupt_payload"]
                        )
                    ).model_dump(mode="json")
                    if isinstance(context.metadata.get("interrupt_payload"), dict)
                    else None
                ),
            },
        )

    def _build_paused_output(self, context: RunContext, result: Any) -> AgentRunOutput:
        """Build paused output envelope for pending interrupt."""
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=RunStatus.PAUSED,
            events=self._deps.event_log.list_for_run(context.run_id),
            tool_trace=result.traces,
            interrupt=result.interrupt,
            metadata={
                "graph_id": self.graph_id,
                "tool_results": context.metadata.get("tool_results", []),
                "approval_payload": ApprovalPayload.from_interrupt(
                    result.interrupt
                ).model_dump(mode="json"),
            },
        )


__all__ = ["SingleAgentOutputMixin"]
