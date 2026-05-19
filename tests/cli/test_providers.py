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


def test_build_openai_compatible_uses_env_fallbacks() -> None:
    """OpenAI-compatible provider should resolve base/model/key from env."""
    provider = build_cli_provider(
        CliProviderConfig(provider="openai-compatible"),
        environ={
            "AGENT_DRIVER_OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            "AGENT_DRIVER_OPENAI_MODEL": "openai/gpt-4o-mini",
            "AGENT_DRIVER_OPENAI_API_KEY": "secret",
        },
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.status.configured is True


def test_build_openai_compatible_uses_api_key_env_name() -> None:
    """Explicit api-key-env should override default API key lookup path."""
    provider = build_cli_provider(
        CliProviderConfig(
            provider="openai-compatible",
            base_url="https://openrouter.ai/api/v1",
            model="openai/gpt-4o-mini",
            api_key_env="OPENROUTER_API_KEY",
        ),
        environ={"OPENROUTER_API_KEY": "secret"},
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.status.configured is True


def test_build_ollama_uses_env_fallbacks() -> None:
    """Ollama provider should resolve base URL and model from env."""
    provider = build_cli_provider(
        CliProviderConfig(provider="ollama"),
        environ={
            "AGENT_DRIVER_OLLAMA_BASE_URL": "http://localhost:11434",
            "AGENT_DRIVER_OLLAMA_MODEL": "llama3.2:3b",
        },
    )
    assert isinstance(provider, OllamaProvider)
    assert provider.status.configured is True


def test_build_openai_compatible_requires_base_model_and_key() -> None:
    """Missing OpenAI-compatible settings should raise helpful config error."""
    with pytest.raises(CliProviderConfigError, match="missing required settings"):
        _ = build_cli_provider(
            CliProviderConfig(provider="openai-compatible"),
            environ={},
        )
