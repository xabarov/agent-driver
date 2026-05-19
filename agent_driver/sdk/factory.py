"""SDK construction helpers with default runtime/tool wiring."""

from __future__ import annotations

from copy import deepcopy
import os

from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.runner import SingleAgentRunner
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tools import wrap_governed_executor
from agent_driver.sdk.agent import Agent, AgentDefaults
from agent_driver.sdk.config import SdkConfig
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    ToolSet,
    register_builtin_tools,
    register_planning_tool,
)


def build_default_registry() -> ToolRegistry:
    """Build default built-in registry for SDK agents."""
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
    agent_id: str = "agent",
    graph_preset: str = "single_react",
) -> Agent:
    """Create SDK Agent facade with filtered tool registry."""
    config_copy = deepcopy(config) if config is not None else RunnerConfig()
    source_registry = config_copy.tool_registry or build_default_registry()
    selected_tools = tools or ToolSet.all()
    filtered_registry = selected_tools.apply(source_registry)
    config_copy.tool_registry = filtered_registry
    config_copy.tool_executor = wrap_governed_executor(
        GovernedToolExecutor(registry=filtered_registry)
    )
    runner = SingleAgentRunner(
        provider=provider,
        checkpoint_store=checkpoint_store or InMemoryCheckpointStore(),
        event_log=event_log or InMemoryEventLog(),
        config=config_copy,
    )
    return Agent(
        runner,
        defaults=AgentDefaults(agent_id=agent_id, graph_preset=graph_preset),
    )


def sdk_config_from_env() -> SdkConfig:
    """Resolve minimal SDK bootstrap config from env."""
    return SdkConfig(
        run_live_tests=os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "0").strip() == "1",
        runtime_store_kind=os.getenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory"),
        openai_base_url=os.getenv("AGENT_DRIVER_OPENAI_BASE_URL"),
        openai_model=os.getenv("AGENT_DRIVER_OPENAI_MODEL"),
    )


__all__ = ["build_default_registry", "create_agent", "sdk_config_from_env"]
