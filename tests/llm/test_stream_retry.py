"""Tests for stream open retry behavior in provider base."""

from __future__ import annotations

import ssl

import httpx
import pytest

from agent_driver.llm.base import HttpClientConfig, ProviderBase, StreamRequest
from agent_driver.llm.contracts import LlmProviderKind
from agent_driver.llm.error_classifier import ProviderErrorReason, classify


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


class _RetryOnceRawTransport(httpx.AsyncBaseTransport):
    """Raise a raw (non-httpx) transport error once, then succeed."""

    def __init__(self, error: BaseException) -> None:
        self.calls = 0
        self._error = error

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            raise self._error
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"line1\nline2\n",
        )


class _AlwaysFailRawTransport(httpx.AsyncBaseTransport):
    """Always raise a raw (non-httpx) transport error on stream open."""

    def __init__(self, error: BaseException) -> None:
        self.calls = 0
        self._error = error

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:  # noqa: ARG002
        self.calls += 1
        raise self._error


def _provider_with(transport: httpx.AsyncBaseTransport) -> ProviderBase:
    return ProviderBase(
        config=ProviderBase.Config(
            name="test",
            kind=LlmProviderKind.FAKE,
            configured=True,
            http_client_config=HttpClientConfig(transport=transport),
        )
    )


def _stream_request() -> StreamRequest:
    # Mirrors the real providers, which pass ``(httpx.HTTPError, ValueError)``:
    # raw OSError stream-open failures must be normalized to an httpx error so
    # this telemetry filter still records them.
    return StreamRequest(
        timeout_s=5.0,
        method="GET",
        url="https://example.com/stream",
        handled_exceptions=(httpx.HTTPError, ValueError),
    )


@pytest.mark.asyncio
async def test_stream_client_with_telemetry_retries_once_on_remote_protocol_error() -> (
    None
):
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
        handled_exceptions=(
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.HTTPError,
        ),
    )

    async with provider.stream_client_with_telemetry(request) as lines:
        chunks = [line async for line in lines]

    assert chunks == ["line1", "line2"]
    assert transport.calls == 2
    assert provider.status.request_count == 1
    assert provider.status.error_count == 0


@pytest.mark.asyncio
async def test_stream_client_with_telemetry_raises_after_retry_budget_exhausted() -> (
    None
):
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
        handled_exceptions=(
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.HTTPError,
        ),
    )

    with pytest.raises(httpx.RemoteProtocolError):
        async with provider.stream_client_with_telemetry(request):
            pass
    assert provider.status.request_count == 1
    assert provider.status.error_count == 1


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLError("[SSL] record layer failure (_ssl.c:2580)"),
        ConnectionResetError("connection reset by peer"),
        OSError("raw socket failure"),
    ],
    ids=["ssl_error", "connection_reset", "oserror"],
)
@pytest.mark.asyncio
async def test_stream_open_retries_once_on_raw_transport_error(
    error: BaseException,
) -> None:
    """A first raw TLS/OSError stream-open failure is retried and then succeeds."""
    transport = _RetryOnceRawTransport(error)
    provider = _provider_with(transport)

    async with provider.stream_client_with_telemetry(_stream_request()) as lines:
        chunks = [line async for line in lines]

    assert chunks == ["line1", "line2"]
    assert transport.calls == 2  # first raw failure retried, second succeeds
    assert provider.status.request_count == 1
    assert provider.status.error_count == 0


@pytest.mark.parametrize(
    "error",
    [
        ssl.SSLError("[SSL] record layer failure (_ssl.c:2580)"),
        ConnectionResetError("connection reset by peer"),
        OSError("raw socket failure"),
    ],
    ids=["ssl_error", "connection_reset", "oserror"],
)
@pytest.mark.asyncio
async def test_repeated_raw_transport_error_normalized_to_transport_error(
    error: BaseException,
) -> None:
    """Exhausting the retry budget surfaces a classified transport failure.

    The raw ``ssl.SSLError`` / ``OSError`` must not crash untyped: it is
    normalized to ``httpx.TransportError`` (with the original error chained),
    so telemetry records a failure and ``error_classifier`` resolves TRANSPORT.
    """
    transport = _AlwaysFailRawTransport(error)
    provider = _provider_with(transport)

    with pytest.raises(httpx.TransportError) as excinfo:
        async with provider.stream_client_with_telemetry(_stream_request()):
            pass

    raised = excinfo.value
    # Not a bare ssl.SSLError / OSError leaking through untyped.
    assert isinstance(raised, httpx.TransportError)
    assert raised.__cause__ is error
    assert transport.calls == 2  # 1 initial + 1 stream-open retry
    assert provider.status.request_count == 1
    assert provider.status.error_count == 1
    # Upper layers classify it consistently as a transport failure.
    assert classify(raised).reason is ProviderErrorReason.TRANSPORT
