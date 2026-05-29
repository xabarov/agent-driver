"""Dependency container functions for FastAPI routes."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, ToolPreset
from app.services.agent_factory import AgentBundle, create_agent_bundle


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return singleton settings object loaded from env."""
    return Settings()


@lru_cache(maxsize=32)
def get_agent_bundle_for_preset(tool_preset: ToolPreset, model: str | None = None) -> AgentBundle:
    """Return cached runtime bundle for one tool preset and optional model."""
    return create_agent_bundle(get_settings(), tool_preset=tool_preset, model=model)


def get_agent_bundle_for_request(
    tool_preset: ToolPreset,
    model: str | None = None,
) -> AgentBundle:
    """Resolve bundle for API request overrides."""
    return get_agent_bundle_for_preset(tool_preset, model)


def get_agent_bundle() -> AgentBundle:
    """Return runtime bundle for the default env tool preset."""
    return get_agent_bundle_for_preset(get_settings().tool_preset)


def resolve_tool_preset(requested: ToolPreset | None) -> ToolPreset:
    """Pick effective tool preset from request override or settings."""
    return requested or get_settings().tool_preset


def reset_dependency_caches() -> None:
    """Clear singleton caches (mainly for tests)."""
    from app.services.agent_factory import (
        get_shared_runtime_store_bundle,
        get_shared_session_store,
    )

    get_agent_bundle_for_preset.cache_clear()
    get_settings.cache_clear()
    get_shared_runtime_store_bundle.cache_clear()
    get_shared_session_store.cache_clear()
    from app.run_cancel import reset_caches_for_tests

    reset_caches_for_tests()
