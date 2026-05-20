"""Dependency container functions for FastAPI routes."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings
from app.services.agent_factory import AgentBundle, create_agent_bundle


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return singleton settings object loaded from env."""
    return Settings()


@lru_cache(maxsize=1)
def get_agent_bundle() -> AgentBundle:
    """Return singleton runtime objects used by API handlers."""
    return create_agent_bundle(get_settings())


def reset_dependency_caches() -> None:
    """Clear singleton caches (mainly for tests)."""
    get_agent_bundle.cache_clear()
    get_settings.cache_clear()

