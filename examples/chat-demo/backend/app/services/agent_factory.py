"""Factory for SDK agent + storage + session dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import Settings, ToolPreset
from app.run_cancel import cancellation_probe
from app.services.fake_scenarios import build_fake_scenario_provider

from agent_driver.cli.providers import CliProviderConfig, build_cli_provider
from agent_driver.cli.sessions import SessionStore
from agent_driver.cli.tools import CliToolConfig, build_cli_toolset
from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.contracts.tools import ToolManifest
from agent_driver.runtime import (
    create_runtime_store_bundle,
    runtime_store_config_from_env,
)
from agent_driver.runtime.control import (
    CommandQueueStore,
    InMemoryCommandQueueStore,
    SqliteCommandQueueStore,
)
from agent_driver.runtime.single_agent.config_sections import (
    CompactionSettings,
    PythonToolSettings,
    SubagentSettings,
    TrimmingSettings,
)
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.llm import (
    DecisionClientSettings,
    DecisionTelemetry,
    JevProductionLabelLedger,
    JevQualityGate,
    JevRolloutController,
    JevRolloutSettings,
    JevTierRouter,
    OpenRouterDecisionClient,
)
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.sdk import Agent, create_agent


@lru_cache(maxsize=1)
def get_shared_runtime_store_bundle():
    """Return one runtime store bundle shared across tool presets."""
    return create_runtime_store_bundle(runtime_store_config_from_env())


@lru_cache(maxsize=1)
def get_shared_session_store(sessions_path: str) -> SessionStore:
    """Return one session store shared across tool presets."""
    return SessionStore(path=Path(sessions_path))


@lru_cache(maxsize=1)
def get_shared_command_queue_store(
    kind: str, sqlite_path: str | None
) -> CommandQueueStore:
    """Return one steering command queue shared across tool presets."""
    if kind == "sqlite":
        path = (
            Path(sqlite_path).with_suffix(".command_queue.sqlite3")
            if sqlite_path
            else Path.cwd() / ".agent-driver" / "command_queue.sqlite3"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteCommandQueueStore(path=str(path))
    return InMemoryCommandQueueStore()


@dataclass(frozen=True, slots=True)
class AgentBundle:
    """Container with long-lived backend dependencies."""

    agent: Agent
    event_log: RuntimeEventLog
    checkpoint_store: CheckpointStore
    command_queue_store: CommandQueueStore
    session_store: SessionStore
    manifests: tuple[ToolManifest, ...]
    store_kind: str
    jev_decision_client: OpenRouterDecisionClient | None = None
    jev_telemetry: DecisionTelemetry | None = None
    jev_label_ledger: JevProductionLabelLedger | None = None


def _tool_config_from_preset(preset: ToolPreset) -> CliToolConfig:
    if preset == "off":
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool",),
            tool_packs=("planning_progress",),
            enable_python=True,
        )
    if preset == "web_search":
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool", "web_search"),
            tool_packs=("planning_progress",),
            enable_python=True,
        )
    if preset == "web_fetch":
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool", "web_fetch"),
            tool_packs=("planning_progress",),
            enable_python=True,
        )
    if preset in {"web", "safe"}:
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool",),
            tool_packs=("web", "planning_progress", "discovery"),
            enable_python=True,
        )
    if preset == "agents":
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool",),
            tool_packs=("planning_progress",),
            enable_python=True,
        )
    if preset == "workspace":
        return CliToolConfig(
            tools_mode="default",
            tool_packs=("web", "planning_progress", "filesystem_read"),
            enable_python=True,
        )
    if preset == "research_light":
        return CliToolConfig(
            tools_mode="default",
            tool_packs=("web", "planning_progress"),
            enable_python=False,
        )
    if preset in {"deep_research", "deep_research_medium"}:
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool", "skill_tool", "skill_view"),
            tool_packs=(
                "web",
                "planning_progress",
                "filesystem_read",
                "filesystem_write",
                "artifacts",
            ),
            allow_dangerous_tools=True,
            enable_python=False,
        )
    if preset == "deep_research_hard":
        return CliToolConfig(
            tools_mode="default",
            tools=("agent_tool", "skill_tool", "skill_view"),
            tool_packs=(
                "web",
                "source_tools",
                "planning_progress",
                "filesystem_read",
                "filesystem_write",
                "artifacts",
            ),
            allow_dangerous_tools=True,
            enable_python=False,
        )
    if preset == "dev":
        return CliToolConfig(
            tools_mode="default",
            tool_packs=(
                "web",
                "planning",
                "filesystem_read",
                "filesystem_write",
                "shell",
            ),
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
    provider = (
        build_fake_scenario_provider(settings.fake_scenario)
        if settings.provider == "fake"
        else None
    )
    if provider is None:
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
    command_queue_store = get_shared_command_queue_store(
        runtime_store_config.kind,
        runtime_store_config.sqlite_path,
    )
    synthetic_compaction_probe = settings.fake_scenario in {
        "compaction_notice",
        "compaction_after_skill_invocation",
    }
    jev_rollout = None
    jev_router = None
    jev_quality_gate = None
    jev_decision_client = None
    jev_telemetry = None
    jev_label_ledger = JevProductionLabelLedger(settings.jev_label_ledger_path)
    jev_model_map: dict[str, str] = {}
    if settings.jev_rollout_mode != "off" and settings.api_key and settings.base_url:
        jev_telemetry = DecisionTelemetry()
        decision_provider = OpenRouterDecisionClient(
            api_key=settings.api_key,
            model=settings.jev_model,
            base_url=settings.base_url,
            settings=DecisionClientSettings(
                timeout_s=min(settings.provider_timeout_seconds, 30.0),
                max_attempts=1,
            ),
            reuse_connections=True,
            telemetry=jev_telemetry,
        )
        jev_decision_client = decision_provider
        allowlist = tuple(
            item.strip()
            for item in settings.jev_task_allowlist.split(",")
            if item.strip()
        )
        gate_modes = {}
        for item in settings.jev_gate_modes.split(","):
            if "=" not in item:
                continue
            gate, mode = (part.strip() for part in item.split("=", 1))
            if gate and mode:
                gate_modes[gate] = mode
        rollout_settings = JevRolloutSettings(
            mode=settings.jev_rollout_mode,
            task_allowlist=allowlist,
            gate_modes=gate_modes,
            pinned_models={
                "routing": settings.jev_model,
                "quality": settings.jev_model,
            },
            max_fallback_rate=settings.jev_max_fallback_rate,
            max_latency_p95_ms=settings.jev_max_latency_p95_ms,
            max_cost_usd=settings.jev_max_cost_usd,
            min_observations_for_rollback=settings.jev_min_observations_for_rollback,
        )
        jev_rollout = JevRolloutController(rollout_settings)
        jev_router = JevTierRouter(
            decision_provider=decision_provider,
            model=settings.jev_model,
        )
        jev_quality_gate = JevQualityGate(
            decision_provider=decision_provider,
            model=settings.jev_model,
        )
        for role, role_model in (
            ("fast", settings.fast_model or settings.model),
            ("balanced", settings.balanced_model or settings.model),
            ("strong", settings.strong_model or settings.model),
        ):
            if role_model:
                jev_model_map[role] = role_model
    runner_config = RunnerConfig(
        cancellation_probe=cancellation_probe,
        trimming=(
            TrimmingSettings(
                token_warning_threshold=1,
                token_compact_threshold=1,
                token_blocking_threshold=1_000_000,
            )
            if synthetic_compaction_probe
            else TrimmingSettings()
        ),
        compaction=CompactionSettings(
            enable_compaction=synthetic_compaction_probe,
            enable_llm_compaction=False,
            enable_session_memory_compaction=False,
        ),
        python_tool=PythonToolSettings(
            enabled=True,
            backend="local",
            allow_overlay=False,
            limits=CodeAgentLimits(max_exec_ms=3_000, max_output_chars=2_000),
        ),
        subagents=SubagentSettings(
            enable_subagents=True,
            max_child_runs=settings.max_child_runs,
        ),
        model_role_map=jev_model_map,
        model_router=jev_router,
        quality_gate=jev_quality_gate,
        jev_rollout=jev_rollout,
    )
    agent = create_agent(
        provider=provider,
        tools=toolset,
        config=runner_config,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        event_log=runtime_store_bundle.event_log,
        command_queue_store=command_queue_store,
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
        command_queue_store=command_queue_store,
        session_store=session_store,
        manifests=manifests,
        store_kind=runtime_store_config.kind,
        jev_decision_client=jev_decision_client,
        jev_telemetry=jev_telemetry,
        jev_label_ledger=jev_label_ledger,
    )
