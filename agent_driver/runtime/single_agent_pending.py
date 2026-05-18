"""Pure helpers for pending interrupt serialization and resume argument merge."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import ResumeAction, ToolPolicyDecision
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.runtime.single_agent_types import PendingInterruptState
from agent_driver.runtime.tools import ToolExecutionResult


def pending_interrupt_from_metadata(
    metadata: dict[str, Any],
) -> PendingInterruptState | None:
    """Parse pending interrupt tuple from checkpoint metadata."""
    payload = metadata.get("pending_interrupt")
    if not isinstance(payload, dict):
        return None
    interrupt_raw = payload.get("interrupt")
    call_raw = payload.get("call")
    envelope_raw = payload.get("envelope")
    if not (
        isinstance(interrupt_raw, dict)
        and isinstance(call_raw, dict)
        and isinstance(envelope_raw, dict)
    ):
        return None
    return PendingInterruptState(
        interrupt=InterruptRequest.model_validate(interrupt_raw),
        call=ToolCall.model_validate(call_raw),
        envelope=ToolResultEnvelope.model_validate(envelope_raw),
    )


def serialize_pending_interrupt(
    state: PendingInterruptState,
) -> dict[str, dict[str, Any]]:
    """Serialize pending interrupt for checkpoint metadata."""
    return {
        "interrupt": state.interrupt.model_dump(mode="json"),
        "call": state.call.model_dump(mode="json"),
        "envelope": state.envelope.model_dump(mode="json"),
    }


def apply_resume_to_call(
    call: ToolCall,
    resume_action: ResumeAction,
    edited_tool_args: dict[str, Any] | None,
) -> ToolCall:
    """Merge edited tool args when resume action is EDIT."""
    if resume_action != ResumeAction.EDIT or edited_tool_args is None:
        return call
    return call.model_copy(update={"args": dict(edited_tool_args)})


def pending_interrupt_from_execution_result(
    result: ToolExecutionResult,
) -> PendingInterruptState | None:
    """Extract pending interrupt tuple from governed tool execution."""
    if result.interrupt is None:
        return None
    for envelope in result.envelopes:
        if envelope.decision == ToolPolicyDecision.INTERRUPT:
            return PendingInterruptState(
                interrupt=result.interrupt,
                call=envelope.call,
                envelope=envelope,
            )
    return None
