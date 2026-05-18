"""Tests for runtime store factory and preflight helpers."""

from __future__ import annotations

import os

import pytest

from agent_driver.runtime import (
    RuntimeStoreFactoryConfig,
    create_runtime_store_bundle,
    preflight_runtime_store,
    runtime_store_config_from_env,
)


def test_factory_memory_bundle() -> None:
    """Factory should build in-memory bundle by default."""
    bundle = create_runtime_store_bundle(RuntimeStoreFactoryConfig(kind="memory"))
    assert bundle.capabilities.transactional_writes is False
    assert bundle.checkpoint_store.capabilities().supports_snapshot_debug


def test_factory_sqlite_bundle(tmp_path) -> None:
    """Factory should build sqlite bundle when sqlite kind selected."""
    path = str(tmp_path / "runtime.sqlite3")
    bundle = create_runtime_store_bundle(
        RuntimeStoreFactoryConfig(kind="sqlite", sqlite_path=path)
    )
    assert bundle.capabilities.transactional_writes
    assert bundle.checkpoint_store.latest("missing") is None
    assert bundle.capabilities.supports_retention


def test_factory_postgres_missing_dsn_preflight() -> None:
    """Preflight should fail when postgres selected without DSN."""
    result = preflight_runtime_store(RuntimeStoreFactoryConfig(kind="postgres"))
    assert result.configured is False
    assert result.healthy is False
    assert result.reason is not None


def test_factory_env_config(monkeypatch, tmp_path) -> None:
    """Env config resolver should map AGENT_DRIVER_* vars into config."""
    sqlite_path = str(tmp_path / "env.sqlite3")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "sqlite")
    monkeypatch.setenv("AGENT_DRIVER_SQLITE_PATH", sqlite_path)
    cfg = runtime_store_config_from_env()
    assert cfg.kind == "sqlite"
    assert cfg.sqlite_path == sqlite_path


def test_factory_env_invalid_kind(monkeypatch) -> None:
    """Env config should reject unsupported runtime store kind."""
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "unknown")
    with pytest.raises(ValueError):
        runtime_store_config_from_env()


def test_factory_env_postgres_flags(monkeypatch) -> None:
    """Env config should parse postgres DSN/schema/auto-create flags."""
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "postgres")
    monkeypatch.setenv("AGENT_DRIVER_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setenv("AGENT_DRIVER_POSTGRES_SCHEMA", "agent_driver")
    monkeypatch.setenv("AGENT_DRIVER_POSTGRES_AUTO_CREATE_SCHEMA", "0")
    cfg = runtime_store_config_from_env()
    assert cfg.kind == "postgres"
    assert cfg.postgres_dsn == "postgresql://test"
    assert cfg.postgres_schema == "agent_driver"
    assert cfg.postgres_auto_create_schema is False


def test_factory_env_postgres_flags_case_insensitive(monkeypatch) -> None:
    """Env parser should treat False/TRUE values case-insensitively."""
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "postgres")
    monkeypatch.setenv("AGENT_DRIVER_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setenv("AGENT_DRIVER_POSTGRES_AUTO_CREATE_SCHEMA", "False")
    cfg_false = runtime_store_config_from_env()
    assert cfg_false.postgres_auto_create_schema is False

    monkeypatch.setenv("AGENT_DRIVER_POSTGRES_AUTO_CREATE_SCHEMA", "TRUE")
    cfg_true = runtime_store_config_from_env()
    assert cfg_true.postgres_auto_create_schema is True


def test_factory_env_prefix_override(monkeypatch, tmp_path) -> None:
    """Resolver should support custom env prefix for embedded apps."""
    sqlite_path = str(tmp_path / "custom.sqlite3")
    monkeypatch.setenv("MYAPP_RUNTIME_STORE_KIND", "sqlite")
    monkeypatch.setenv("MYAPP_SQLITE_PATH", sqlite_path)
    cfg = runtime_store_config_from_env(prefix="MYAPP_")
    assert cfg.kind == "sqlite"
    assert cfg.sqlite_path == sqlite_path


def teardown_module() -> None:
    """Clear leaked env vars from tests that directly mutate os.environ."""
    for key in list(os.environ):
        if key.startswith("AGENT_DRIVER_") and "TEST" in key:
            os.environ.pop(key, None)
