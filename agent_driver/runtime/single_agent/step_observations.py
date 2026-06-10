"""Compatibility shim for tool-stage observation helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.tool_stage.observations import (
    build_observations_from_tool_result,
)

__all__ = ["build_observations_from_tool_result"]
