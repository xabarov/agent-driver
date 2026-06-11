"""HttpClientConfig / build_async_client contract (concurrency-safety).

Regression for the explorer "fails at concurrency > 1" bug: a single shared
``transport`` instance is torn down by the first concurrent client's
``aclose()``, breaking sibling in-flight streams. ``verify_ssl`` lets each
client build its OWN default transport (own pool), which is concurrency-safe.
"""

from __future__ import annotations

import httpx
import pytest

from agent_driver.llm.base import HttpClientConfig, ProviderBase
from agent_driver.llm.contracts import LlmProviderKind


def _provider(cfg: HttpClientConfig) -> ProviderBase:
    return ProviderBase(
        config=ProviderBase.Config(
            name="test",
            kind=LlmProviderKind.FAKE,
            configured=True,
            http_client_config=cfg,
        )
    )


@pytest.mark.asyncio
async def test_verify_ssl_clients_do_not_share_a_transport() -> None:
    """With verify_ssl (no custom transport) every build_async_client call gets
    its OWN transport — so one client's aclose() can't tear down another's pool.
    This is the invariant that makes concurrent streaming safe."""
    provider = _provider(HttpClientConfig(verify_ssl=False))
    c1 = provider.build_async_client(timeout_s=5.0)
    c2 = provider.build_async_client(timeout_s=5.0)
    try:
        assert c1._transport is not None
        # The core invariant: distinct transport instances, so one client's
        # aclose() (on async-with exit) cannot tear down another's pool.
        assert c1._transport is not c2._transport
        # Closing one must not raise / affect the other.
        await c1.aclose()
    finally:
        await c1.aclose()
        await c2.aclose()


@pytest.mark.asyncio
async def test_verify_ssl_true_is_the_default() -> None:
    cfg = HttpClientConfig()
    assert cfg.verify_ssl is True
    assert cfg.transport is None


@pytest.mark.asyncio
async def test_custom_transport_still_takes_precedence() -> None:
    """A custom transport (test injection) is used as-is — back-compat for the
    offline MockTransport pattern existing provider tests rely on."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
    provider = _provider(HttpClientConfig(transport=transport, verify_ssl=False))
    client = provider.build_async_client(timeout_s=5.0)
    try:
        assert client._transport is transport
    finally:
        await client.aclose()
