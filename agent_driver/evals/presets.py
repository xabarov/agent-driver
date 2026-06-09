"""Open-weight model presets for low-budget live comparison (T0).

Per the testing plan, live runs go through **OpenRouter** using **open-weight**
models only — frontier models aren't needed to compare harness mechanics at a
fixed model. This module is pure data + a thin :class:`ProviderSpec` builder
(no network, no key access at import); the caller supplies the API key.

Pick a ``mid`` model for success-sensitive metrics and optionally a ``small``
one for cheap behavioral/latency checks. Pin the exact id + temperature in the
suite config and report median over N runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.llm.provider_descriptors import ProviderSpec

# OpenRouter open-weight model ids by rough capability tier. Ids are stable
# OpenRouter slugs; bump them as newer open-weight releases land.
OPENWEIGHT_MODELS: dict[str, str] = {
    "small": "qwen/qwen-2.5-7b-instruct",
    "mid": "qwen/qwen-2.5-72b-instruct",
    "large": "deepseek/deepseek-chat",
}

# Default sampling temperature for comparison runs (>0 so stochasticity is
# exercised; report median over N runs, never best-of-N).
DEFAULT_TEMPERATURE: float = 0.7


@dataclass(frozen=True, slots=True)
class OpenWeightPreset:
    """Resolved preset: a tier label, its model id, and the sampling temp."""

    tier: str
    model: str
    temperature: float = DEFAULT_TEMPERATURE


def openweight_preset(tier: str = "mid") -> OpenWeightPreset:
    """Return the preset for a tier (``small`` / ``mid`` / ``large``)."""
    if tier not in OPENWEIGHT_MODELS:
        raise ValueError(
            f"unknown tier {tier!r}; choose from {sorted(OPENWEIGHT_MODELS)}"
        )
    return OpenWeightPreset(tier=tier, model=OPENWEIGHT_MODELS[tier])


def openweight_provider_spec(
    tier: str = "mid", *, api_key: str | None = None, timeout_s: float = 60.0
) -> ProviderSpec:
    """Build an OpenRouter :class:`ProviderSpec` for an open-weight tier.

    ``api_key`` is the caller's OpenRouter key; when omitted, the descriptor's
    env resolution (``OPENROUTER_API_KEY`` / ``AGENT_DRIVER_API_KEY``) applies
    at build time.
    """
    preset = openweight_preset(tier)
    return ProviderSpec(
        provider_id="openrouter",
        model=preset.model,
        api_key=api_key or "",
        timeout_s=timeout_s,
    )


__all__ = [
    "DEFAULT_TEMPERATURE",
    "OPENWEIGHT_MODELS",
    "OpenWeightPreset",
    "openweight_preset",
    "openweight_provider_spec",
]
