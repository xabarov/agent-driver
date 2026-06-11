"""Descriptor-first provider resolution.

Separates the three concerns that were previously tangled in ad-hoc
``if provider == ...`` chains:

* **metadata** — a :class:`ProviderDescriptor` declares what a provider needs
  (transport, default base URL/model, which env vars carry credentials, what is
  required) without any construction logic;
* **routing** — the caller (CLI/SDK/eval) picks a provider id + overrides
  (a :class:`ProviderSpec`); env fills the gaps;
* **transport** — :func:`resolve_provider` maps the descriptor's transport to a
  concrete provider constructor in ONE place.

Adding a new provider that reuses an existing transport is now a data entry
(register a descriptor), not new dispatch code. Out-of-tree providers register
their own descriptor via :func:`register_provider_descriptor`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from agent_driver.contracts.enums import StrEnum
from agent_driver.llm.providers import LlmProvider
from agent_driver.llm.providers_impl import (
    AnthropicProvider,
    FakeProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from agent_driver.registry import Registry, RegistryError


class ProviderTransport(StrEnum):
    """Concrete wire implementation a descriptor resolves to."""

    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class ProviderResolutionError(ValueError):
    """Raised when a provider cannot be resolved or is misconfigured."""


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:  # pylint: disable=too-many-instance-attributes
    """Declarative metadata for one provider id (a wide config record)."""

    provider_id: str
    transport: ProviderTransport
    aliases: tuple[str, ...] = ()
    default_base_url: str | None = None
    default_model: str | None = None
    requires_base_url: bool = False
    requires_api_key: bool = False
    base_url_env: tuple[str, ...] = ("AGENT_DRIVER_BASE_URL",)
    api_key_env: tuple[str, ...] = ("AGENT_DRIVER_API_KEY",)
    model_env: tuple[str, ...] = ("AGENT_DRIVER_MODEL",)


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """A request to build a provider: an id plus optional overrides."""

    provider_id: str = "fake"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_s: float = 30.0
    fake_response: str = "ok"


_BUILTIN: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(provider_id="fake", transport=ProviderTransport.FAKE),
    ProviderDescriptor(
        provider_id="openrouter",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_base_url="https://openrouter.ai/api/v1",
        requires_api_key=True,
        api_key_env=("AGENT_DRIVER_API_KEY", "OPENROUTER_API_KEY"),
    ),
    ProviderDescriptor(
        provider_id="openai",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        default_base_url="https://api.openai.com/v1",
        requires_api_key=True,
        api_key_env=("AGENT_DRIVER_API_KEY", "OPENAI_API_KEY"),
    ),
    ProviderDescriptor(
        provider_id="vllm",
        transport=ProviderTransport.OPENAI_COMPATIBLE,
        requires_base_url=True,
    ),
    ProviderDescriptor(
        provider_id="ollama",
        transport=ProviderTransport.OLLAMA,
        default_base_url="http://localhost:11434",
        default_model="llama3:8b",
    ),
    ProviderDescriptor(
        provider_id="anthropic",
        transport=ProviderTransport.ANTHROPIC,
        default_base_url="https://api.anthropic.com",
        default_model="claude-3-5-haiku-latest",
        requires_api_key=True,
        api_key_env=("AGENT_DRIVER_API_KEY", "ANTHROPIC_API_KEY"),
    ),
)

_REGISTRY: Registry[ProviderDescriptor] = Registry(kind="provider")


def register_provider_descriptor(
    descriptor: ProviderDescriptor, *, replace_existing: bool = False
) -> None:
    """Register a descriptor under its id and aliases."""
    try:
        _REGISTRY.register(
            descriptor.provider_id,
            descriptor,
            aliases=descriptor.aliases,
            replace=replace_existing,
        )
    except RegistryError as exc:
        raise ProviderResolutionError(
            f"provider descriptor already registered: {descriptor.provider_id!r}"
        ) from exc


def get_provider_descriptor(provider_id: str) -> ProviderDescriptor:
    """Return the descriptor for an id/alias, or raise."""
    descriptor = _REGISTRY.try_get(provider_id)
    if descriptor is None:
        raise ProviderResolutionError(
            f"unknown provider {provider_id!r}; known: "
            + ", ".join(sorted(list_provider_ids()))
        )
    return descriptor


def list_provider_ids() -> tuple[str, ...]:
    """Return the canonical ids of registered providers."""
    return tuple(sorted({d.provider_id for d in _REGISTRY.values()}))


def _first_env(env: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = env.get(key)
        if value:
            return value
    return None


def resolve_provider(
    spec: ProviderSpec, *, env: Mapping[str, str] | None = None
) -> LlmProvider:
    """Build a concrete provider from a spec, filling gaps from env."""
    environ = os.environ if env is None else env
    descriptor = get_provider_descriptor(spec.provider_id)
    if descriptor.transport == ProviderTransport.FAKE:
        return FakeProvider(
            name=descriptor.provider_id, response_text=spec.fake_response
        )

    base_url = (
        spec.base_url
        or _first_env(environ, descriptor.base_url_env)
        or descriptor.default_base_url
    )
    model = (
        spec.model
        or _first_env(environ, descriptor.model_env)
        or descriptor.default_model
    )
    api_key = spec.api_key or _first_env(environ, descriptor.api_key_env)

    missing: list[str] = []
    if descriptor.requires_base_url and not base_url:
        missing.append("base_url")
    if not model:
        missing.append("model")
    if descriptor.requires_api_key and not api_key:
        missing.append("api_key")
    if missing:
        raise ProviderResolutionError(
            f"provider {descriptor.provider_id!r} missing required settings: "
            + ", ".join(missing)
        )

    return _construct(
        descriptor, base_url=base_url, model=model, api_key=api_key, spec=spec
    )


def _construct(
    descriptor: ProviderDescriptor,
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    spec: ProviderSpec,
) -> LlmProvider:
    transport = descriptor.transport
    if transport == ProviderTransport.OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(
            config=OpenAICompatibleProvider.Config(
                name=descriptor.provider_id,
                base_url=base_url or "",
                api_key=api_key,
                model=model or "",
                timeout_s=spec.timeout_s,
            )
        )
    if transport == ProviderTransport.OLLAMA:
        return OllamaProvider(
            config=OllamaProvider.Config(
                name=descriptor.provider_id,
                base_url=base_url or "",
                model=model or "",
                timeout_s=spec.timeout_s,
            )
        )
    if transport == ProviderTransport.ANTHROPIC:
        return AnthropicProvider(
            config=AnthropicProvider.Config(
                name=descriptor.provider_id,
                base_url=base_url or "https://api.anthropic.com",
                api_key=api_key or "",
                model=model or "",
                timeout_s=spec.timeout_s,
            )
        )
    raise ProviderResolutionError(f"unsupported transport: {transport}")


def _reset_descriptors_for_tests() -> None:
    _REGISTRY.clear()
    for descriptor in _BUILTIN:
        register_provider_descriptor(descriptor, replace_existing=True)


# Seed the built-in registry at import time.
for _descriptor in _BUILTIN:
    register_provider_descriptor(_descriptor, replace_existing=True)


__all__ = [
    "ProviderDescriptor",
    "ProviderResolutionError",
    "ProviderSpec",
    "ProviderTransport",
    "get_provider_descriptor",
    "list_provider_ids",
    "register_provider_descriptor",
    "resolve_provider",
]
