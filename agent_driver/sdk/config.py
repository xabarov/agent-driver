"""Typed SDK bootstrap configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SdkTransportConfig:
    """Provider transport defaults exposed through SDK config."""

    timeout_s: float = 60.0
    max_retries: int = 3


@dataclass(frozen=True, slots=True)
class SdkConfig:
    """Environment-derived settings used by SDK embedders."""

    run_live_tests: bool = False
    runtime_store_kind: str = "memory"
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    transport: SdkTransportConfig = SdkTransportConfig()

    @property
    def timeout_s(self) -> float:
        """Default provider timeout in seconds."""
        return self.transport.timeout_s

    @property
    def max_retries(self) -> int:
        """Default provider retry count for SDK-created transports."""
        return self.transport.max_retries


__all__ = ["SdkConfig", "SdkTransportConfig"]
