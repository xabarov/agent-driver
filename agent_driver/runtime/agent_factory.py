"""High-level helper for constructing a runner with selected tool surface."""

from __future__ import annotations

from copy import deepcopy

from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.runner import SingleAgentRunner
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tools import wrap_governed_executor
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    ToolSet,
    register_builtin_tools,
    register_planning_tool,
)


def _default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    return registry


def create_agent(
    *,
    provider: LlmProvider,
    tools: ToolSet | None = None,
    config: RunnerConfig | None = None,
    checkpoint_store: CheckpointStore | None = None,
    event_log: RuntimeEventLog | None = None,
) -> SingleAgentRunner:
    """Create runner with filtered tool registry and governed executor wiring."""
    config_copy = deepcopy(config) if config is not None else RunnerConfig()
    source_registry = config_copy.tool_registry or _default_registry()
    selected_tools = tools or ToolSet.all()
    filtered_registry = selected_tools.apply(source_registry)
    config_copy.tool_registry = filtered_registry
    config_copy.tool_executor = wrap_governed_executor(
        GovernedToolExecutor(registry=filtered_registry)
    )
    return SingleAgentRunner(
        provider=provider,
        checkpoint_store=checkpoint_store or InMemoryCheckpointStore(),
        event_log=event_log or InMemoryEventLog(),
        config=config_copy,
    )


__all__ = ["create_agent"]
