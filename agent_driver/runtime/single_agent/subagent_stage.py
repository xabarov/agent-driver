"""Compatibility shim for subagent execution stage helpers."""

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
