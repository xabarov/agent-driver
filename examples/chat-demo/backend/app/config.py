"""Configuration models for chat demo backend."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ToolPreset = Literal["off", "safe", "dev", "all"]


class Settings(BaseSettings):
    """Environment-backed settings for backend runtime."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("APP_HOST"))
    app_port: int = Field(default=8000, validation_alias=AliasChoices("APP_PORT"))
    app_cors_origins: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("APP_CORS_ORIGINS"),
    )

    tool_preset: ToolPreset = Field(
        default="safe",
        validation_alias=AliasChoices("CHAT_DEMO_TOOL_PRESET"),
    )
    max_steps: int = Field(
        default=24,
        validation_alias=AliasChoices("CHAT_DEMO_MAX_STEPS"),
    )
    max_tool_calls: int = Field(
        default=12,
        validation_alias=AliasChoices("CHAT_DEMO_MAX_TOOL_CALLS"),
    )
    deadline_seconds: float = Field(
        default=180.0,
        validation_alias=AliasChoices("CHAT_DEMO_DEADLINE_SECONDS"),
    )
    stream_poll_interval_ms: int = Field(
        default=20,
        validation_alias=AliasChoices("CHAT_DEMO_STREAM_POLL_INTERVAL_MS"),
    )
    sessions_path: Path = Field(
        default=Path("./.agent-driver/sessions.json"),
        validation_alias=AliasChoices("CHAT_DEMO_SESSIONS_PATH"),
    )

    provider: str = Field(
        default="fake",
        validation_alias=AliasChoices("AGENT_DRIVER_PROVIDER"),
    )
    model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DRIVER_MODEL"),
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
    runtime_store_kind: str = Field(
        default="memory",
        validation_alias=AliasChoices("AGENT_DRIVER_RUNTIME_STORE_KIND"),
    )

    @property
    def cors_origins(self) -> list[str]:
        """Parse comma-separated CORS origins into clean list."""
        return [
            item.strip()
            for item in self.app_cors_origins.split(",")
            if item.strip()
        ] or ["http://localhost:5173"]

