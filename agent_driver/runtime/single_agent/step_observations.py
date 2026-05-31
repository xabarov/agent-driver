"""Compatibility shim for tool-stage observation helpers."""

from agent_driver.runtime.single_agent.tool_stage.observations import (
    build_observations_from_tool_result,
)

__all__ = ["build_observations_from_tool_result"]
