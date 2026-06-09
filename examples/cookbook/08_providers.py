"""Providers: resolve a provider from a descriptor + register a custom one.

Descriptor-first resolution separates metadata (what a provider needs) from
construction. Adding a provider that reuses a transport is a data entry, not
new dispatch code.

    python examples/cookbook/08_providers.py
"""

from __future__ import annotations

from agent_driver.llm import (
    ProviderDescriptor,
    ProviderSpec,
    ProviderTransport,
    list_provider_ids,
    register_provider_descriptor,
    resolve_provider,
)


def main() -> None:
    print("built-in providers:", list_provider_ids())

    # The fake provider needs no config — resolve it offline.
    fake = resolve_provider(ProviderSpec(provider_id="fake"), env={})
    print("resolved:", fake.name)

    # Register a custom OpenAI-compatible gateway with an alias, no new code.
    register_provider_descriptor(
        ProviderDescriptor(
            provider_id="my_gateway",
            transport=ProviderTransport.OPENAI_COMPATIBLE,
            aliases=("mygw",),
            default_base_url="https://gw.example.com/v1",
            requires_api_key=True,
        ),
        replace_existing=True,
    )
    provider = resolve_provider(
        ProviderSpec(provider_id="mygw", model="my-model", api_key="secret"), env={}
    )
    print("custom provider configured:", provider.status.configured)


if __name__ == "__main__":
    main()
