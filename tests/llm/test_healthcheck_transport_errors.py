"""Healthcheck should treat transport/TLS failures as unhealthy, not raise."""

from __future__ import annotations

import ssl

import httpx
import pytest

from agent_driver.llm.base import HttpClientConfig
from agent_driver.llm.providers_impl.ollama import OllamaProvider
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


def _ssl_error_transport() -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise ssl.SSLError("[SSL] record layer failure")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_openai_compatible_healthcheck_ssl_error_is_unhealthy() -> None:
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai-mock",
            base_url="https://mock.local/v1",
            api_key="token",
            model="gpt-test",
            http_client_config=HttpClientConfig(transport=_ssl_error_transport()),
        )
    )
    status = await provider.healthcheck()
    assert status.healthy is False


@pytest.mark.asyncio
async def test_ollama_healthcheck_ssl_error_is_unhealthy() -> None:
    provider = OllamaProvider(
        config=OllamaProvider.Config(
            name="ollama-mock",
            base_url="https://mock.local",
            model="llama3:8b",
            http_client_config=HttpClientConfig(transport=_ssl_error_transport()),
        )
    )
    status = await provider.healthcheck()
    assert status.healthy is False
