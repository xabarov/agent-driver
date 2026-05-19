"""Typed SDK bootstrap configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SdkConfig:
    """Environment-derived settings used by SDK embedders."""

    run_live_tests: bool = False
    runtime_store_kind: str = "memory"
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


__all__ = ["SdkConfig"]
