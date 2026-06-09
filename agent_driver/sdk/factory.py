"""SDK construction helpers with default runtime/tool wiring."""

from __future__ import annotations

import os

from agent_driver.code_agent.backends import create_python_backend
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.llm.providers import LlmProvider
from agent_driver.memory.provider import MemoryProvider
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.control import CommandQueueStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.lifecycle_hooks import RunLifecycleHook
from agent_driver.runtime.runner import SingleAgentRunner
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tools import wrap_governed_executor
from agent_driver.sdk.agent import Agent, AgentDefaults
from agent_driver.sdk.config import SdkConfig, SdkTransportConfig
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    ToolSet,
    register_builtin_tools,
    register_planning_tool,
)


def build_default_registry(config: RunnerConfig | None = None) -> ToolRegistry:
    """Build default built-in registry for SDK agents."""
    settings = (config or RunnerConfig()).python_tool
    python_backend = None
    if settings.enabled:
        python_backend = create_python_backend(
            settings.backend,
            session_idle_seconds=settings.session_idle_seconds,
        )
    registry = ToolRegistry()
    register_builtin_tools(
        registry,
        python_backend=python_backend,
        python_settings=settings,
    )
    register_planning_tool(registry)
    return registry


def create_agent(
    *,
    provider: LlmProvider,
    tools: ToolSet | None = None,
    config: RunnerConfig | None = None,
    checkpoint_store: CheckpointStore | None = None,
    event_log: RuntimeEventLog | None = None,
    command_queue_store: CommandQueueStore | None = None,
    memory_provider: MemoryProvider | None = None,
    lifecycle_hooks: tuple[RunLifecycleHook, ...] | None = None,
    agent_id: str = "agent",
    graph_preset: str = "single_react",
) -> Agent:
    """Create SDK Agent facade with filtered tool registry."""
    # Shallow override-copy (not deepcopy): keeps the caller's config intact
    # while letting us attach stateful deps (memory provider, registries) that
    # are not safe to deep-copy.
    config_copy = (config or RunnerConfig()).with_overrides()
    effective_memory = memory_provider
    if effective_memory is None and config is not None:
        effective_memory = getattr(config, "memory_provider", None)
    config_copy.memory_provider = effective_memory
    if lifecycle_hooks is not None:
        config_copy.lifecycle_hooks = tuple(lifecycle_hooks)
    if command_queue_store is not None:
        config_copy.command_queue_store = command_queue_store
    source_registry = config_copy.tool_registry or build_default_registry(config_copy)
    selected_tools = tools or ToolSet.all()
    selected_tools.validate_known_names(source_registry)
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
        command_queue_store=command_queue_store,
    )


async def query(
    text: str,
    *,
    provider: LlmProvider,
    tools: ToolSet | None = None,
    config: RunnerConfig | None = None,
    checkpoint_store: CheckpointStore | None = None,
    event_log: RuntimeEventLog | None = None,
    command_queue_store: CommandQueueStore | None = None,
    memory_provider: MemoryProvider | None = None,
    lifecycle_hooks: tuple[RunLifecycleHook, ...] | None = None,
    agent_id: str = "agent",
    graph_preset: str = "single_react",
    run_id: str | None = None,
    app_metadata: dict[str, object] | None = None,
) -> AgentRunOutput:
    """One-shot top-level SDK query helper."""
    agent = create_agent(
        provider=provider,
        tools=tools,
        config=config,
        checkpoint_store=checkpoint_store,
        event_log=event_log,
        command_queue_store=command_queue_store,
        memory_provider=memory_provider,
        lifecycle_hooks=lifecycle_hooks,
        agent_id=agent_id,
        graph_preset=graph_preset,
    )
    return await agent.query(
        text,
        run_id=run_id,
        app_metadata=app_metadata,
    )


def sdk_config_from_env() -> SdkConfig:
    """Resolve minimal SDK bootstrap config from env."""
    return SdkConfig(
        run_live_tests=os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "0").strip() == "1",
        runtime_store_kind=os.getenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory"),
        provider=os.getenv("AGENT_DRIVER_PROVIDER"),
        base_url=os.getenv("AGENT_DRIVER_BASE_URL"),
        model=os.getenv("AGENT_DRIVER_MODEL"),
        api_key=os.getenv("AGENT_DRIVER_API_KEY"),
        transport=SdkTransportConfig(
            timeout_s=float(os.getenv("AGENT_DRIVER_TIMEOUT_S", "60")),
            max_retries=int(os.getenv("AGENT_DRIVER_MAX_RETRIES", "3")),
        ),
    )


__all__ = ["build_default_registry", "create_agent", "query", "sdk_config_from_env"]
