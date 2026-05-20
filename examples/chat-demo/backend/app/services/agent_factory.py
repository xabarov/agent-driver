"""Factory for SDK agent + storage + session dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agent_driver.cli.providers import CliProviderConfig, build_cli_provider
from agent_driver.cli.sessions import SessionStore
from agent_driver.cli.tools import CliToolConfig, build_cli_toolset
from agent_driver.contracts.tools import ToolManifest
from agent_driver.runtime import create_runtime_store_bundle, runtime_store_config_from_env
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.sdk import Agent, create_agent

from app.config import Settings, ToolPreset
from app.run_cancel import cancellation_probe


@lru_cache(maxsize=1)
def get_shared_runtime_store_bundle():
    """Return one runtime store bundle shared across tool presets."""
    return create_runtime_store_bundle(runtime_store_config_from_env())


@lru_cache(maxsize=1)
def get_shared_session_store(sessions_path: str) -> SessionStore:
    """Return one session store shared across tool presets."""
    return SessionStore(path=Path(sessions_path))


@dataclass(frozen=True, slots=True)
class AgentBundle:
    """Container with long-lived backend dependencies."""

    agent: Agent
    event_log: RuntimeEventLog
    checkpoint_store: CheckpointStore
    session_store: SessionStore
    manifests: tuple[ToolManifest, ...]
    store_kind: str


def _tool_config_from_preset(preset: ToolPreset) -> CliToolConfig:
    if preset == "off":
        return CliToolConfig(tools_mode="none")
    if preset == "safe":
        return CliToolConfig(tools_mode="default")
    if preset == "dev":
        return CliToolConfig(
            tools_mode="default",
            tool_packs=("filesystem_write", "shell"),
            allow_dangerous_tools=True,
        )
    return CliToolConfig(
        tools_mode="all",
        allow_dangerous_tools=True,
    )


def create_agent_bundle(
    settings: Settings,
    *,
    tool_preset: ToolPreset | None = None,
    model: str | None = None,
) -> AgentBundle:
    """Build provider, stores, filtered toolset, and SDK facade."""
    effective_preset = tool_preset or settings.tool_preset
    effective_model = model or settings.model
    provider = build_cli_provider(
        CliProviderConfig(
            provider=settings.provider,
            model=effective_model,
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout_s=settings.provider_timeout_seconds,
        )
    )
    toolset = build_cli_toolset(_tool_config_from_preset(effective_preset))
    runtime_store_config = runtime_store_config_from_env()
    runtime_store_bundle = get_shared_runtime_store_bundle()
    runner_config = RunnerConfig(cancellation_probe=cancellation_probe)
    agent = create_agent(
        provider=provider,
        tools=toolset,
        config=runner_config,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        event_log=runtime_store_bundle.event_log,
    )
    registry = agent.runner.config.tool_registry
    manifests: tuple[ToolManifest, ...] = ()
    if registry is not None:
        manifests = tuple(item.manifest for item in registry.list_registered())
    session_store = get_shared_session_store(str(settings.sessions_path))
    return AgentBundle(
        agent=agent,
        event_log=runtime_store_bundle.event_log,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        session_store=session_store,
        manifests=manifests,
        store_kind=runtime_store_config.kind,
    )

