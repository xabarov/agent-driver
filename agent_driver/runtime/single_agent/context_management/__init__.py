"""Context-management helpers for the single-agent runtime."""

from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    CompactionStageHost,
    apply_compaction_if_eligible,
)

__all__ = ["CompactionStageHost", "apply_compaction_if_eligible"]
