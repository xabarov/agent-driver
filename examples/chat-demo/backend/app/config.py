"""Configuration models for chat demo backend."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ToolPreset = Literal[
    "off",
    "web_search",
    "web_fetch",
    "web",
    "research_light",
    "deep_research",
    "deep_research_medium",
    "deep_research_hard",
    "agents",
    # Legacy/internal presets remain accepted for backend scenarios and older clients.
    "safe",
    "workspace",
    "dev",
    "all",
]
PlanningMode = Literal[
    "off",
    "prompt_only",
    "required_for_writes",
    "required_for_risky_tools",
    "always_for_multistep",
]
JevRolloutMode = Literal["off", "shadow", "active"]


def _discover_env_files() -> tuple[str, ...]:
    """Load demo env from repo root / chat-demo / backend (first found, later overrides)."""
    chat_demo_root = Path(__file__).resolve().parents[2]
    repo_root = chat_demo_root.parents[1]
    candidates = (
        repo_root / ".env",
        chat_demo_root / ".env",
        chat_demo_root / "backend" / ".env",
        Path(".env"),
    )
    return tuple(str(path) for path in candidates if path.is_file())


class Settings(BaseSettings):
    """Environment-backed settings for backend runtime."""

    model_config = SettingsConfigDict(
        env_file=_discover_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = Field(
        default="127.0.0.1", validation_alias=AliasChoices("APP_HOST")
    )
    app_port: int = Field(default=8010, validation_alias=AliasChoices("APP_PORT"))
    app_cors_origins: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("APP_CORS_ORIGINS"),
    )
    tracing_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CHAT_DEMO_TRACING_ENABLED"),
    )
    phoenix_project_name: str = Field(
        default="agent-driver-chat-demo",
        validation_alias=AliasChoices("PHOENIX_PROJECT_NAME"),
    )
    phoenix_collector_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHOENIX_COLLECTOR_ENDPOINT"),
    )

    tool_preset: ToolPreset = Field(
        default="web",
        validation_alias=AliasChoices("CHAT_DEMO_TOOL_PRESET"),
    )
    force_planning: bool = Field(
        default=False,
        validation_alias=AliasChoices("CHAT_DEMO_FORCE_PLANNING"),
    )
    force_planning_mode: PlanningMode = Field(
        default="required_for_writes",
        validation_alias=AliasChoices(
            "CHAT_DEMO_FORCE_PLANNING_MODE",
            "CHAT_DEMO_PLANNING_MODE",
        ),
    )
    max_steps: int = Field(
        default=24,
        validation_alias=AliasChoices("CHAT_DEMO_MAX_STEPS"),
    )
    max_tool_calls: int = Field(
        default=24,
        validation_alias=AliasChoices("CHAT_DEMO_MAX_TOOL_CALLS"),
    )
    deadline_seconds: float = Field(
        default=600.0,
        validation_alias=AliasChoices("CHAT_DEMO_DEADLINE_SECONDS"),
    )
    max_child_runs: int = Field(
        default=3,
        validation_alias=AliasChoices("CHAT_DEMO_MAX_CHILD_RUNS"),
    )
    deep_research_phase_gate_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("CHAT_DEMO_DEEP_RESEARCH_PHASE_GATE_ENABLED"),
    )
    stream_poll_interval_ms: int = Field(
        default=20,
        validation_alias=AliasChoices("CHAT_DEMO_STREAM_POLL_INTERVAL_MS"),
    )
    llm_stream_idle_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices("CHAT_DEMO_LLM_STREAM_IDLE_TIMEOUT_SECONDS"),
    )
    sse_keepalive_seconds: float = Field(
        default=15.0,
        validation_alias=AliasChoices("CHAT_DEMO_SSE_KEEPALIVE_SECONDS"),
    )
    sessions_path: Path = Field(
        default=Path("./.agent-driver/sessions.json"),
        validation_alias=AliasChoices("CHAT_DEMO_SESSIONS_PATH"),
    )
    workspace_root: Path = Field(
        default=Path("workspace"),
        validation_alias=AliasChoices("CHAT_DEMO_WORKSPACE_ROOT"),
    )

    provider: str = Field(
        default="fake",
        validation_alias=AliasChoices("AGENT_DRIVER_PROVIDER"),
    )
    fake_scenario: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CHAT_DEMO_FAKE_SCENARIO"),
    )
    model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_MODEL"),
    )
    fast_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_FAST_MODEL"),
    )
    balanced_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_BALANCED_MODEL"),
    )
    strong_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_STRONG_MODEL"),
    )
    base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_BASE_URL"),
    )
    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_API_KEY"),
    )
    provider_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("AGENT_DRIVER_TIMEOUT_SECONDS"),
    )
    jev_rollout_mode: JevRolloutMode = Field(
        default="off",
        validation_alias=AliasChoices("CHAT_DEMO_JEV_ROLLOUT_MODE"),
    )
    jev_model: str = Field(
        default="typesafe/jev-1.13",
        validation_alias=AliasChoices("AGENT_DRIVER_JEV_MODEL", "JEV_MODEL"),
    )
    jev_task_allowlist: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_DEMO_JEV_TASK_ALLOWLIST"),
    )
    jev_gate_modes: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_DEMO_JEV_GATE_MODES"),
    )
    jev_max_latency_p95_ms: float = Field(
        default=5000.0,
        validation_alias=AliasChoices("CHAT_DEMO_JEV_MAX_LATENCY_P95_MS"),
    )
    jev_max_fallback_rate: float = Field(
        default=0.05,
        validation_alias=AliasChoices("CHAT_DEMO_JEV_MAX_FALLBACK_RATE"),
    )
    jev_max_cost_usd: float = Field(
        default=0.005,
        validation_alias=AliasChoices("CHAT_DEMO_JEV_MAX_COST_USD"),
    )
    jev_min_observations_for_rollback: int = Field(
        default=5,
        validation_alias=AliasChoices("CHAT_DEMO_JEV_MIN_OBSERVATIONS_FOR_ROLLBACK"),
    )
    jev_label_ledger_path: Path = Field(
        default=Path(".agent-driver/jev-production-labels.jsonl"),
        validation_alias=AliasChoices("CHAT_DEMO_JEV_LABEL_LEDGER"),
    )
    runtime_store_kind: str = Field(
        default="memory",
        validation_alias=AliasChoices("AGENT_DRIVER_RUNTIME_STORE_KIND"),
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parse comma-separated CORS origins into clean list."""
        return [
            item.strip() for item in self.app_cors_origins.split(",") if item.strip()
        ] or ["http://localhost:5173"]
