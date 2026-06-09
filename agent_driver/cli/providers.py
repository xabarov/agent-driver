"""Provider bootstrap helpers for CLI commands.

Thin CLI adapter over the descriptor-first resolver in
:mod:`agent_driver.llm.provider_descriptors`: it gathers settings from flags /
env into a :class:`CliProviderConfig`, then delegates construction to
``resolve_provider`` so the CLI shares one source of truth with the SDK and
evals (and gets every registered provider — including ``anthropic`` — for free).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from agent_driver.llm.provider_descriptors import (
    ProviderResolutionError,
    ProviderSpec,
    resolve_provider,
)
from agent_driver.llm.providers import LlmProvider


class CliProviderConfigError(ValueError):
    """Raised when CLI provider settings are invalid."""


DEFAULT_LIVE_EVAL_TIMEOUT_S = 300.0
_DEFAULT_CLI_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class CliProviderConfig:
    """Provider configuration gathered from CLI flags and environment."""

    provider: str = "fake"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_s: float = 30.0
    fake_response: str = "ok"


def provider_config_for_eval(config: CliProviderConfig) -> CliProviderConfig:
    """Apply live-eval-friendly defaults (longer HTTP timeout for real providers)."""
    if config.provider == "fake":
        return config
    if config.timeout_s > _DEFAULT_CLI_TIMEOUT_S:
        return config
    env_raw = os.environ.get("AGENT_DRIVER_PROVIDER_TIMEOUT_S")
    if env_raw:
        try:
            timeout_s = float(env_raw)
        except ValueError:
            timeout_s = DEFAULT_LIVE_EVAL_TIMEOUT_S
    else:
        timeout_s = DEFAULT_LIVE_EVAL_TIMEOUT_S
    return CliProviderConfig(
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_s=timeout_s,
        fake_response=config.fake_response,
    )


def build_cli_provider(
    config: CliProviderConfig, *, environ: Mapping[str, str] | None = None
) -> LlmProvider:
    """Build a provider from CLI config via the descriptor resolver."""
    env = dict(os.environ if environ is None else environ)
    spec = ProviderSpec(
        provider_id=config.provider,
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_s=config.timeout_s,
        fake_response=config.fake_response,
    )
    try:
        return resolve_provider(spec, env=env)
    except ProviderResolutionError as exc:
        raise CliProviderConfigError(str(exc)) from exc


__all__ = [
    "DEFAULT_LIVE_EVAL_TIMEOUT_S",
    "CliProviderConfig",
    "CliProviderConfigError",
    "build_cli_provider",
    "provider_config_for_eval",
]
