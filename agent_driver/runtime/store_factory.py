"""Factory and preflight helpers for runtime storage backends."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal

from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.sqlite_store import SqliteRuntimeStore
from agent_driver.runtime.storage import (
    CheckpointStore,
    RuntimeEventLog,
    StorageCapabilities,
)

RuntimeStoreKind = Literal["memory", "sqlite", "postgres"]


@dataclass(frozen=True)
class RuntimeStoreFactoryConfig:
    """Config for selecting and creating runtime storage backend."""

    kind: RuntimeStoreKind = "memory"
    sqlite_path: str | None = None
    postgres_dsn: str | None = None
    postgres_schema: str = "public"
    postgres_auto_create_schema: bool = True


@dataclass(frozen=True)
class RuntimeStoreBundle:
    """Pair of checkpoint/event stores for one backend selection."""

    checkpoint_store: CheckpointStore
    event_log: RuntimeEventLog
    capabilities: StorageCapabilities


@dataclass(frozen=True)
class RuntimeStorePreflightResult:
    """Preflight result for storage backend readiness."""

    kind: RuntimeStoreKind
    configured: bool
    healthy: bool
    reason: str | None = None
    capabilities: StorageCapabilities | None = None


def _postgres_bundle(config: RuntimeStoreFactoryConfig) -> RuntimeStoreBundle:
    """Create postgres storage bundle lazily to keep base install lightweight."""
    if not config.postgres_dsn:
        raise ValueError("postgres_dsn is required for postgres runtime store")
    pg_module = import_module("agent_driver.runtime.postgres_store")
    postgres_runtime_store_cls = pg_module.PostgresRuntimeStore
    postgres_runtime_store_config_cls = pg_module.PostgresRuntimeStoreConfig

    store = postgres_runtime_store_cls(
        config=postgres_runtime_store_config_cls(
            dsn=config.postgres_dsn,
            auto_create_schema=config.postgres_auto_create_schema,
            schema=config.postgres_schema,
        )
    )
    return RuntimeStoreBundle(
        checkpoint_store=store,
        event_log=store,
        capabilities=store.capabilities(),
    )


def create_runtime_store_bundle(
    config: RuntimeStoreFactoryConfig,
) -> RuntimeStoreBundle:
    """Create checkpoint/event stores from backend config."""
    if config.kind == "memory":
        checkpoint_store = InMemoryCheckpointStore()
        event_log = InMemoryEventLog()
        return RuntimeStoreBundle(
            checkpoint_store=checkpoint_store,
            event_log=event_log,
            capabilities=checkpoint_store.capabilities(),
        )
    if config.kind == "sqlite":
        sqlite_path = config.sqlite_path or str(Path.cwd() / ".runtime_store.sqlite3")
        store = SqliteRuntimeStore(path=sqlite_path)
        return RuntimeStoreBundle(
            checkpoint_store=store,
            event_log=store,
            capabilities=store.capabilities(),
        )
    if config.kind == "postgres":
        return _postgres_bundle(config)
    raise ValueError(f"Unsupported runtime store kind '{config.kind}'")


def runtime_store_config_from_env(
    prefix: str = "AGENT_DRIVER_",
) -> RuntimeStoreFactoryConfig:
    """Build factory config from environment variables."""
    kind = os.getenv(f"{prefix}RUNTIME_STORE_KIND", "memory").strip().lower()
    if kind not in {"memory", "sqlite", "postgres"}:
        raise ValueError(
            f"{prefix}RUNTIME_STORE_KIND must be one of memory|sqlite|postgres"
        )
    sqlite_path = os.getenv(f"{prefix}SQLITE_PATH")
    postgres_dsn = os.getenv(f"{prefix}POSTGRES_DSN")
    postgres_schema = os.getenv(f"{prefix}POSTGRES_SCHEMA", "public")
    postgres_auto_create_raw = os.getenv(f"{prefix}POSTGRES_AUTO_CREATE_SCHEMA", "1")
    postgres_auto_create_schema = postgres_auto_create_raw.strip() not in {"0", "false"}
    return RuntimeStoreFactoryConfig(
        kind=kind,  # type: ignore[arg-type]
        sqlite_path=sqlite_path,
        postgres_dsn=postgres_dsn,
        postgres_schema=postgres_schema,
        postgres_auto_create_schema=postgres_auto_create_schema,
    )


def preflight_runtime_store(
    config: RuntimeStoreFactoryConfig,
) -> RuntimeStorePreflightResult:
    """Check storage backend readiness without mutating runner logic."""
    try:
        bundle = create_runtime_store_bundle(config)
    except (RuntimeError, ValueError, TypeError) as exc:  # pragma: no cover
        return RuntimeStorePreflightResult(
            kind=config.kind,
            configured=False,
            healthy=False,
            reason=str(exc),
            capabilities=None,
        )
    return RuntimeStorePreflightResult(
        kind=config.kind,
        configured=True,
        healthy=True,
        capabilities=bundle.capabilities,
    )
