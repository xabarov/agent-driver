"""Compatibility shim for single-agent runtime config sections."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.lifecycle.config_sections import (
    CodeAgentSettings,
    CompactionSettings,
    PythonToolSettings,
    SubagentSettings,
    TrimmingSettings,
)

__all__ = [
    "CodeAgentSettings",
    "CompactionSettings",
    "PythonToolSettings",
    "SubagentSettings",
    "TrimmingSettings",
]
