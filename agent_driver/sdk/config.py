"""Typed SDK bootstrap configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SdkConfig:
    """Environment-derived settings used by SDK embedders."""

    run_live_tests: bool = False
    runtime_store_kind: str = "memory"
    openai_base_url: str | None = None
    openai_model: str | None = None


__all__ = ["SdkConfig"]
