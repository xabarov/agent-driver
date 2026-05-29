"""CLI configuration loading and profile resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import tomllib
from typing import Any


DEFAULT_PROVIDER = "openrouter"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-5.4"


@dataclass(frozen=True, slots=True)
class CliConfig:
    """Resolved CLI config values from layered sources."""

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_s: float | None = None
    tools: str | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None
    deadline_seconds: float | None = None
    store_kind: str | None = None
    sqlite_path: str | None = None
    postgres_dsn: str | None = None
    enable_python: bool | None = None
    python_backend: str | None = None
    python_allow_imports: str | None = None


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _from_section(payload: dict[str, Any]) -> CliConfig:
    cli = payload.get("cli")
    if not isinstance(cli, dict):
        cli = {}
    return CliConfig(
        provider=_as_str(cli.get("provider")),
        model=_as_str(cli.get("model")),
        base_url=_as_str(cli.get("base_url")),
        timeout_s=_as_float(cli.get("timeout_s")),
        tools=_as_str(cli.get("tools")),
        max_steps=_as_int(cli.get("max_steps")),
        max_tool_calls=_as_int(cli.get("max_tool_calls")),
        deadline_seconds=_as_float(cli.get("deadline_seconds")),
        store_kind=_as_str(cli.get("store_kind")),
        sqlite_path=_as_str(cli.get("sqlite_path")),
        postgres_dsn=_as_str(cli.get("postgres_dsn")),
        enable_python=_as_bool(cli.get("enable_python")),
        python_backend=_as_str(cli.get("python_backend")),
        python_allow_imports=_as_str(cli.get("python_allow_imports")),
    )


def load_cli_config(*, cwd: Path | None = None) -> CliConfig:
    """Load layered CLI config (user then project override)."""
    current = cwd or Path.cwd()
    user_path = Path.home() / ".config" / "agent-driver" / "config.toml"
    project_path = current / ".agent-driver.toml"
    user = _from_section(_read_toml(user_path))
    project = _from_section(_read_toml(project_path))
    return merge_cli_config(user, project)


def merge_cli_config(*configs: CliConfig) -> CliConfig:
    """Merge configs left-to-right where latter wins."""
    merged: dict[str, Any] = {}
    for cfg in configs:
        for key, value in asdict(cfg).items():
            if value is not None:
                merged[key] = value
    return CliConfig(**merged)


def config_to_dict(config: CliConfig) -> dict[str, Any]:
    """Render config as plain dictionary for JSON output."""
    return {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "timeout_s": config.timeout_s,
        "tools": config.tools,
        "max_steps": config.max_steps,
        "max_tool_calls": config.max_tool_calls,
        "deadline_seconds": config.deadline_seconds,
        "store_kind": config.store_kind,
        "sqlite_path": config.sqlite_path,
        "postgres_dsn": bool(config.postgres_dsn),
        "enable_python": config.enable_python,
        "python_backend": config.python_backend,
        "python_allow_imports": config.python_allow_imports,
    }


def load_local_dotenv(*, cwd: Path | None = None) -> None:
    """Load a local .env file into os.environ without overwriting existing values."""
    dotenv_path = (cwd or Path.cwd()) / ".env"
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_with_env(config: CliConfig, *, cwd: Path | None = None) -> CliConfig:
    """Fill config with env values where missing."""
    load_local_dotenv(cwd=cwd)
    return merge_cli_config(
        CliConfig(
            provider=DEFAULT_PROVIDER,
            model=DEFAULT_MODEL,
            base_url=DEFAULT_BASE_URL,
            tools="default",
        ),
        config,
        CliConfig(
            provider=_as_str(os.environ.get("AGENT_DRIVER_PROVIDER")),
            model=_as_str(os.environ.get("AGENT_DRIVER_MODEL")),
            base_url=_as_str(os.environ.get("AGENT_DRIVER_BASE_URL")),
            timeout_s=_as_float(os.environ.get("AGENT_DRIVER_PROVIDER_TIMEOUT_S")),
            sqlite_path=_as_str(os.environ.get("AGENT_DRIVER_SQLITE_PATH")),
            postgres_dsn=_as_str(os.environ.get("AGENT_DRIVER_POSTGRES_DSN")),
            enable_python=_as_bool(os.environ.get("AGENT_DRIVER_ENABLE_PYTHON")),
            python_backend=_as_str(os.environ.get("AGENT_DRIVER_PYTHON_BACKEND")),
            python_allow_imports=_as_str(
                os.environ.get("AGENT_DRIVER_PYTHON_ALLOW_IMPORTS")
            ),
        ),
    )


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


__all__ = [
    "CliConfig",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "config_to_dict",
    "load_local_dotenv",
    "load_cli_config",
    "merge_cli_config",
    "resolve_with_env",
]
