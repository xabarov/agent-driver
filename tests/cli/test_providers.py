"""Tests for CLI provider bootstrap configuration and env resolution."""

from __future__ import annotations

import pytest

from agent_driver.cli.providers import (
    CliProviderConfig,
    CliProviderConfigError,
    build_cli_provider,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.providers_impl.ollama import OllamaProvider
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


def test_build_cli_provider_defaults_to_fake() -> None:
    """CLI provider builder should default to deterministic fake provider."""
    provider = build_cli_provider(CliProviderConfig(), environ={})
    assert isinstance(provider, FakeProvider)


def test_build_openrouter_uses_env_fallbacks() -> None:
    """OpenRouter provider should resolve base/model/key from env."""
    provider = build_cli_provider(
        CliProviderConfig(provider="openrouter"),
        environ={
            "AGENT_DRIVER_BASE_URL": "https://openrouter.ai/api/v1",
            "AGENT_DRIVER_MODEL": "openai/gpt-4o-mini",
            "AGENT_DRIVER_API_KEY": "secret",
        },
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.status.configured is True


def test_build_vllm_allows_missing_api_key() -> None:
    """vLLM provider should work with local OpenAI-compatible servers."""
    provider = build_cli_provider(
        CliProviderConfig(
            provider="vllm",
            base_url="http://localhost:8000/v1",
            model="local-model",
        ),
        environ={},
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.status.configured is True


def test_build_ollama_uses_env_fallbacks() -> None:
    """Ollama provider should resolve base URL and model from env."""
    provider = build_cli_provider(
        CliProviderConfig(provider="ollama"),
        environ={
            "AGENT_DRIVER_BASE_URL": "http://localhost:11434",
            "AGENT_DRIVER_MODEL": "llama3.2:3b",
        },
    )
    assert isinstance(provider, OllamaProvider)
    assert provider.status.configured is True


def test_build_openai_compatible_requires_base_model_and_key() -> None:
    """Missing OpenRouter settings should raise helpful config error."""
    with pytest.raises(CliProviderConfigError, match="missing required settings"):
        _ = build_cli_provider(
            CliProviderConfig(provider="openrouter"),
            environ={},
        )
