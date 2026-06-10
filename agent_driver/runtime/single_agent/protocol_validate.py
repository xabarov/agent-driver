"""Compatibility shim for protocol validation helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.context_management.protocol_validate import (
    ProtocolValidationResult,
    validate_and_repair_protocol_messages,
)

__all__ = ["ProtocolValidationResult", "validate_and_repair_protocol_messages"]
