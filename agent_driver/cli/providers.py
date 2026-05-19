"""Provider bootstrap helpers for CLI commands."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

from agent_driver.llm.providers import LlmProvider
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.providers_impl.ollama import OllamaProvider
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


class CliProviderConfigError(ValueError):
    """Raised when CLI provider settings are invalid."""


@dataclass(frozen=True, slots=True)
class CliProviderConfig:
    """Provider configuration gathered from CLI flags and environment."""

    provider: str = "fake"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_s: float = 30.0
    fake_response: str = "ok"


def _first_present(environ: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = environ.get(key)
        if value:
            return value
    return None


def _resolve_api_key(config: CliProviderConfig, environ: Mapping[str, str]) -> str | None:
    if config.api_key:
        return config.api_key
    return environ.get("AGENT_DRIVER_API_KEY")


def _build_openai_compatible(
    config: CliProviderConfig, environ: Mapping[str, str], *, provider_name: str
) -> LlmProvider:
    base_url = config.base_url or environ.get("AGENT_DRIVER_BASE_URL")
    model = config.model or environ.get("AGENT_DRIVER_MODEL")
    api_key = _resolve_api_key(config, environ)
    missing: list[str] = []
    if not base_url:
        missing.append("base_url")
    if not model:
        missing.append("model")
    if provider_name == "openrouter" and not api_key:
        missing.append("api_key")
    if missing:
        raise CliProviderConfigError(
            f"{provider_name} provider missing required settings: "
            + ", ".join(missing)
            + ". Use flags or env vars AGENT_DRIVER_API_KEY, "
            "AGENT_DRIVER_BASE_URL, AGENT_DRIVER_MODEL."
        )
    return OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name=provider_name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=config.timeout_s,
        )
    )


def _build_ollama(config: CliProviderConfig, environ: Mapping[str, str]) -> LlmProvider:
    base_url = config.base_url or environ.get("AGENT_DRIVER_BASE_URL") or "http://localhost:11434"
    model = config.model or environ.get("AGENT_DRIVER_MODEL") or "llama3:8b"
    return OllamaProvider(
        config=OllamaProvider.Config(
            name="ollama",
            base_url=base_url,
            model=model,
            timeout_s=config.timeout_s,
        )
    )


def build_cli_provider(
    config: CliProviderConfig, *, environ: Mapping[str, str] | None = None
) -> LlmProvider:
    """Build provider instance from normalized CLI provider config."""
    env = dict(os.environ if environ is None else environ)
    if config.provider == "fake":
        return FakeProvider(response_text=config.fake_response)
    if config.provider in {"openrouter", "vllm"}:
        return _build_openai_compatible(config, env, provider_name=config.provider)
    if config.provider == "ollama":
        return _build_ollama(config, env)
    raise CliProviderConfigError(
        f"Unsupported provider '{config.provider}'. Use fake|openrouter|vllm|ollama."
    )


__all__ = ["CliProviderConfig", "CliProviderConfigError", "build_cli_provider"]
