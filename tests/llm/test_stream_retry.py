"""Tests for stream open retry behavior in provider base."""

from __future__ import annotations

import pytest
import httpx

from agent_driver.llm.base import HttpClientConfig, ProviderBase, StreamRequest
from agent_driver.llm.contracts import LlmProviderKind


class _RetryOnceTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            raise httpx.RemoteProtocolError("server disconnected")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"line1\nline2\n",
        )


class _AlwaysFailTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:  # noqa: ARG002
        raise httpx.RemoteProtocolError("server disconnected")


@pytest.mark.asyncio
async def test_stream_client_with_telemetry_retries_once_on_remote_protocol_error() -> None:
    transport = _RetryOnceTransport()
    provider = ProviderBase(
        config=ProviderBase.Config(
            name="test",
            kind=LlmProviderKind.FAKE,
            configured=True,
            http_client_config=HttpClientConfig(transport=transport),
        )
    )
    request = StreamRequest(
        timeout_s=5.0,
        method="GET",
        url="https://example.com/stream",
        handled_exceptions=(httpx.RemoteProtocolError, httpx.ReadError, httpx.HTTPError),
    )

    async with provider.stream_client_with_telemetry(request) as lines:
        chunks = [line async for line in lines]

    assert chunks == ["line1", "line2"]
    assert transport.calls == 2
    assert provider.status.request_count == 1
    assert provider.status.error_count == 0


@pytest.mark.asyncio
async def test_stream_client_with_telemetry_raises_after_retry_budget_exhausted() -> None:
    provider = ProviderBase(
        config=ProviderBase.Config(
            name="test",
            kind=LlmProviderKind.FAKE,
            configured=True,
            http_client_config=HttpClientConfig(transport=_AlwaysFailTransport()),
        )
    )
    request = StreamRequest(
        timeout_s=5.0,
        method="GET",
        url="https://example.com/stream",
        handled_exceptions=(httpx.RemoteProtocolError, httpx.ReadError, httpx.HTTPError),
    )

    with pytest.raises(httpx.RemoteProtocolError):
        async with provider.stream_client_with_telemetry(request):
            pass
    assert provider.status.request_count == 1
    assert provider.status.error_count == 1
