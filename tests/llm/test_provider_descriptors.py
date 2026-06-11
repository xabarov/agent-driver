"""Tests for descriptor-first provider resolution."""

from __future__ import annotations

import pytest

from agent_driver.llm.provider_descriptors import (
    ProviderDescriptor,
    ProviderResolutionError,
    ProviderSpec,
    ProviderTransport,
    _reset_descriptors_for_tests,
    get_provider_descriptor,
    list_provider_ids,
    register_provider_descriptor,
    resolve_provider,
)
from agent_driver.llm.providers_impl import (
    AnthropicProvider,
    FakeProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    _reset_descriptors_for_tests()


def test_fake_needs_no_config() -> None:
    provider = resolve_provider(ProviderSpec(provider_id="fake"), env={})
    assert isinstance(provider, FakeProvider)


def test_openrouter_from_env() -> None:
    provider = resolve_provider(
        ProviderSpec(provider_id="openrouter"),
        env={"AGENT_DRIVER_MODEL": "openai/gpt-4o-mini", "OPENROUTER_API_KEY": "k"},
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.status.configured is True  # default base_url filled


def test_vllm_requires_base_url_allows_no_key() -> None:
    provider = resolve_provider(
        ProviderSpec(
            provider_id="vllm", base_url="http://localhost:8000/v1", model="m"
        ),
        env={},
    )
    assert isinstance(provider, OpenAICompatibleProvider)


def test_vllm_missing_base_url_errors() -> None:
    with pytest.raises(ProviderResolutionError, match="base_url"):
        resolve_provider(ProviderSpec(provider_id="vllm", model="m"), env={})


def test_ollama_defaults() -> None:
    provider = resolve_provider(ProviderSpec(provider_id="ollama"), env={})
    assert isinstance(provider, OllamaProvider)  # default base_url + model


def test_anthropic_now_available() -> None:
    provider = resolve_provider(
        ProviderSpec(provider_id="anthropic", api_key="sk"), env={}
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.status.configured is True  # default model filled


def test_openrouter_missing_model_and_key_errors() -> None:
    with pytest.raises(ProviderResolutionError, match="missing required settings"):
        resolve_provider(ProviderSpec(provider_id="openrouter"), env={})


def test_unknown_provider_errors() -> None:
    with pytest.raises(ProviderResolutionError, match="unknown provider"):
        resolve_provider(ProviderSpec(provider_id="nope"), env={})


def test_spec_overrides_win_over_env() -> None:
    provider = resolve_provider(
        ProviderSpec(provider_id="openrouter", model="explicit", api_key="x"),
        env={"AGENT_DRIVER_MODEL": "from-env"},
    )
    assert isinstance(provider, OpenAICompatibleProvider)


def test_register_custom_descriptor_with_alias() -> None:
    register_provider_descriptor(
        ProviderDescriptor(
            provider_id="my_gateway",
            transport=ProviderTransport.OPENAI_COMPATIBLE,
            aliases=("mygw",),
            default_base_url="https://gw.example.com/v1",
            requires_api_key=True,
        )
    )
    assert "my_gateway" in list_provider_ids()
    assert get_provider_descriptor("mygw").provider_id == "my_gateway"
    provider = resolve_provider(
        ProviderSpec(provider_id="mygw", model="m", api_key="k"), env={}
    )
    assert isinstance(provider, OpenAICompatibleProvider)


def test_duplicate_registration_rejected_without_replace() -> None:
    with pytest.raises(ProviderResolutionError, match="already registered"):
        register_provider_descriptor(
            ProviderDescriptor(provider_id="fake", transport=ProviderTransport.FAKE)
        )
