"""SDK construction helpers with default runtime/tool wiring."""

from __future__ import annotations

import dataclasses
import os

from agent_driver.code_agent.backends import create_python_backend
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.llm.model_router import ModelRouter
from agent_driver.llm.providers import LlmProvider
from agent_driver.memory.provider import MemoryProvider
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.control import CommandQueueStore
from agent_driver.runtime.control import InMemoryCommandQueueStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.lifecycle_hooks import RunLifecycleHook
from agent_driver.runtime.runner import SingleAgentRunner
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.runtime.tools import wrap_governed_executor
from agent_driver.sdk.agent import Agent, AgentDefaults
from agent_driver.sdk.config import SdkConfig, SdkTransportConfig
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    ToolSet,
    register_builtin_tools,
    register_memory_tool,
    register_planning_tool,
)


def build_default_registry(config: RunnerConfig | None = None) -> ToolRegistry:
    """Build default built-in registry for SDK agents."""
    cfg = config or RunnerConfig()
    settings = cfg.python_tool
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
    # Epic M1: expose the model-callable `remember` tool only when a memory
    # provider is configured — without a store to flush to it would be a silent
    # no-op. Off by default, on exactly when long-term memory is wired.
    if getattr(cfg, "memory_provider", None) is not None:
        register_memory_tool(registry)
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
    tool_gate: ToolGate | None = None,
    agent_id: str = "agent",
    graph_preset: str = "single_react",
    model_role_map: dict[str, str] | None = None,
    model_router: ModelRouter | None = None,
    role_providers: dict[str, LlmProvider] | None = None,
) -> Agent:
    """Create SDK Agent facade with filtered tool registry.

    ``tool_gate`` becomes the agent's construction-time default gate: every
    run/stream/session turn uses it unless that call passes its own gate, so a
    permission gate is wired once instead of on every call.

    ``model_role_map`` (R2, role→model), ``model_router`` (R5/R6, a
    :class:`~agent_driver.llm.model_router.ModelRouter` that picks the role per
    turn) and ``role_providers`` (R3, role→provider) are build-path sugar for the
    R-track: pass them here instead of hand-constructing a ``RunnerConfig``. Each
    is applied only when non-``None`` and overrides the same field on ``config``
    (the capabilities object is replaced, never mutated, so a caller's shared
    ``config`` is untouched).
    """
    # Shallow override-copy (not deepcopy): keeps the caller's config intact
    # while letting us attach stateful deps (memory provider, registries) that
    # are not safe to deep-copy.
    config_copy = (config or RunnerConfig()).with_overrides()
    # R-track routing sugar. capabilities is a frozen dataclass shared by the
    # shallow copy, so we reassign a replaced instance (a safe top-level override)
    # rather than mutate it; role_providers is a plain top-level attribute.
    capability_overrides: dict[str, object] = {}
    if model_role_map is not None:
        capability_overrides["model_role_map"] = dict(model_role_map)
    if model_router is not None:
        capability_overrides["model_router"] = model_router
    if capability_overrides:
        config_copy.capabilities = dataclasses.replace(
            config_copy.capabilities, **capability_overrides
        )
    if role_providers is not None:
        config_copy.role_providers = dict(role_providers)
    effective_memory = memory_provider
    if effective_memory is None and config is not None:
        effective_memory = getattr(config, "memory_provider", None)
    config_copy.memory_provider = effective_memory
    if lifecycle_hooks is not None:
        config_copy.lifecycle_hooks = tuple(lifecycle_hooks)
    effective_command_store = (
        command_queue_store
        or getattr(config_copy, "command_queue_store", None)
        or InMemoryCommandQueueStore()
    )
    config_copy.command_queue_store = effective_command_store
    source_registry = config_copy.tool_registry or build_default_registry(config_copy)
    selected_tools = tools or ToolSet.all()
    selected_tools.validate_known_names(source_registry)
    filtered_registry = selected_tools.apply(source_registry)
    config_copy.tool_registry = filtered_registry
    config_copy.tool_executor = wrap_governed_executor(
        GovernedToolExecutor(
            registry=filtered_registry,
            concurrency_limit=config_copy.tool_concurrency_limit,
            artifact_store=config_copy.artifact_store,
            per_turn_output_budget_chars=config_copy.capabilities.per_turn_output_budget_chars,
        )
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
        command_queue_store=effective_command_store,
        default_tool_gate=tool_gate,
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
    tool_gate: ToolGate | None = None,
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
        tool_gate=tool_gate,
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
        api_key=(
            os.getenv("AGENT_DRIVER_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("LLM_API_KEY")
        ),
        transport=SdkTransportConfig(
            timeout_s=float(os.getenv("AGENT_DRIVER_TIMEOUT_S", "60")),
            max_retries=int(os.getenv("AGENT_DRIVER_MAX_RETRIES", "3")),
        ),
    )


__all__ = ["build_default_registry", "create_agent", "query", "sdk_config_from_env"]
