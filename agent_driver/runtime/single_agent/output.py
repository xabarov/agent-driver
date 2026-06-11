"""Compatibility shim for single-agent final output helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.finalization.output import SingleAgentOutputMixin

__all__ = ["SingleAgentOutputMixin"]
