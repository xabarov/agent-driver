"""Compatibility shim for pending subagent helpers."""

from agent_driver.runtime.single_agent.lifecycle.pending import (
    apply_resume_to_call,
    pending_interrupt_from_execution_result,
    pending_interrupt_from_metadata,
    serialize_pending_interrupt,
)

__all__ = [
    "apply_resume_to_call",
    "pending_interrupt_from_execution_result",
    "pending_interrupt_from_metadata",
    "serialize_pending_interrupt",
]
