"""Compatibility shim for subagent execution stage helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.tool_stage.subagent_execution import (
    SubagentStageHost,
    _apply_skill_preloads,
    maybe_execute_subagent_group,
)

__all__ = [
    "SubagentStageHost",
    "_apply_skill_preloads",
    "maybe_execute_subagent_group",
]
