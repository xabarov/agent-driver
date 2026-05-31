"""Planning-related tool-stage event projection."""

from __future__ import annotations

from typing import Protocol

from agent_driver.contracts.enums import InterruptReason, RuntimeEventType
from agent_driver.runtime.planning_check import is_exit_plan_mode_tool
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.types import EventSpec, RunContext
from agent_driver.runtime.tools import ToolExecutionResult


class ToolStagePlanningHost(Protocol):
    """Host surface required for planning lifecycle event helpers."""

    def _emit(self, event: EventSpec) -> None: ...


def emit_plan_lifecycle_events(
    host: ToolStagePlanningHost, context: RunContext, result: ToolExecutionResult
) -> None:
    """Emit plan-mode and plan-approval lifecycle events from tool results."""
    for envelope in result.envelopes:
        if envelope.call.tool_name == "enter_plan_mode":
            emit_step_event(
                host,
                context,
                event_type=RuntimeEventType.PLAN_MODE_ENTERED,
                payload={
                    "tool_call_id": envelope.call.tool_call_id,
                    "summary": envelope.summary,
                },
            )
            continue
        if not is_exit_plan_mode_tool(envelope.call.tool_name):
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        plan_payload = structured.get("plan_approval")
        if not isinstance(plan_payload, dict):
            continue
        payload = {
            "tool_call_id": envelope.call.tool_call_id,
            "plan_id": plan_payload.get("plan_id"),
            "content_hash": plan_payload.get("content_hash"),
            "path": plan_payload.get("path"),
        }
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.PLAN_ARTIFACT_UPDATED,
            payload=payload,
        )
        if (
            result.interrupt is not None
            and result.interrupt.reason == InterruptReason.PLAN_APPROVAL_REQUIRED
        ):
            emit_step_event(
                host,
                context,
                event_type=RuntimeEventType.PLAN_APPROVAL_REQUESTED,
                payload={
                    **payload,
                    "interrupt_id": result.interrupt.interrupt_id,
                },
            )


__all__ = ["emit_plan_lifecycle_events"]
