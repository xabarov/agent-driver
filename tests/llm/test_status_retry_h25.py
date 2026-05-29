"""Phase 13 H25 — tests for HTTP-status-code-based retry in ProviderBase.

Pins:
  * 429 with Retry-After: integer → wait that many seconds, retry succeeds;
  * 429 without Retry-After → exponential backoff (1s / 2s / 4s);
  * 503 (Loading model, the litellm cascade pattern) → retried;
  * 502, 504 → retried;
  * 4xx other than 429 → NOT retried, raises immediately;
  * out of retry budget → raises HTTPStatusError;
  * malformed Retry-After → falls back to exp schedule;
  * HTTP-date Retry-After → parsed and honored.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent_driver.llm.base import (
    HttpClientConfig,
    ProviderBase,
    StreamRequest,
    _parse_retry_after,
    _status_retry_delay,
)
from agent_driver.llm.contracts import LlmProviderKind


# ---------------------------------------------------------------------------
# Pure-function tests for the retry-after parser.
# ---------------------------------------------------------------------------


def test_parse_retry_after_seconds():
    assert _parse_retry_after("3") == 3.0
    assert _parse_retry_after("0.5") == 0.5
    assert _parse_retry_after("32") == 32.0


def test_parse_retry_after_caps_at_32_seconds():
    # The schedule cap protects callers from a server returning 1h (3600s).
    assert _parse_retry_after("3600") == 32.0


def test_parse_retry_after_rejects_negative():
    assert _parse_retry_after("-5") is None


def test_parse_retry_after_returns_none_on_missing_or_empty():
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("   ") is None


def test_parse_retry_after_http_date_in_future():
    when = datetime.now(timezone.utc) + timedelta(seconds=5)
    raw = format_datetime(when, usegmt=True)
    out = _parse_retry_after(raw)
    assert out is not None
    # The server-rounded date arrives ~1s after we compute, so the parsed
    # delta will be ~4-6s. Just assert the bound.
    assert 0 <= out <= 10


def test_parse_retry_after_http_date_in_past_returns_zero():
    when = datetime.now(timezone.utc) - timedelta(seconds=30)
    raw = format_datetime(when, usegmt=True)
    assert _parse_retry_after(raw) == 0.0


def test_parse_retry_after_malformed_returns_none():
    assert _parse_retry_after("not a number nor a date") is None


def test_status_retry_delay_uses_schedule_when_no_retry_after():
    assert _status_retry_delay(1, None) == 1.0
    assert _status_retry_delay(2, None) == 2.0
    assert _status_retry_delay(3, None) == 4.0
    assert _status_retry_delay(99, None) == 32.0


def test_status_retry_delay_honors_retry_after():
    assert _status_retry_delay(1, retry_after=7.5) == 7.5
    assert _status_retry_delay(99, retry_after=0.0) == 0.0


# ---------------------------------------------------------------------------
# Integration tests via mocked httpx transport.
# ---------------------------------------------------------------------------


class _StatusSequenceTransport(httpx.AsyncBaseTransport):
    """Returns a scripted sequence of (status, headers, body) responses."""

    def __init__(
        self, sequence: list[tuple[int, dict[str, str], bytes]]
    ) -> None:
        self._sequence = sequence
        self.calls = 0

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:  # noqa: ARG002
        idx = min(self.calls, len(self._sequence) - 1)
        status, headers, body = self._sequence[idx]
        self.calls += 1
        return httpx.Response(status, headers=headers, content=body)


def _make_provider(transport: httpx.AsyncBaseTransport) -> ProviderBase:
    return ProviderBase(
        config=ProviderBase.Config(
            name="test",
            kind=LlmProviderKind.FAKE,
            configured=True,
            http_client_config=HttpClientConfig(transport=transport),
        )
    )


def _make_request() -> StreamRequest:
    return StreamRequest(
        timeout_s=5.0,
        method="GET",
        url="https://example.com/stream",
        handled_exceptions=(
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.HTTPError,
        ),
    )


@pytest.mark.asyncio
async def test_stream_retries_once_on_503_then_succeeds():
    """The litellm 503 'Loading model' pattern. Retries with exp backoff."""
    transport = _StatusSequenceTransport(
        [
            (503, {}, b""),
            (200, {"content-type": "text/event-stream"}, b"line1\nline2\n"),
        ]
    )
    provider = _make_provider(transport)
    with patch.object(asyncio, "sleep", new=AsyncMock(return_value=None)) as sleep_mock:
        async with provider.stream_client_with_telemetry(_make_request()) as lines:
            chunks = [line async for line in lines]
    assert chunks == ["line1", "line2"]
    assert transport.calls == 2
    # One delay between attempt 1 (503) and attempt 2 (200).
    assert sleep_mock.await_count == 1
    # First retry's delay = 1.0s (exp schedule entry [0]).
    assert sleep_mock.await_args_list[0].args[0] == 1.0


@pytest.mark.asyncio
async def test_stream_retries_on_429_honoring_retry_after():
    transport = _StatusSequenceTransport(
        [
            (429, {"retry-after": "2"}, b""),
            (200, {"content-type": "text/event-stream"}, b"ok\n"),
        ]
    )
    provider = _make_provider(transport)
    with patch.object(asyncio, "sleep", new=AsyncMock(return_value=None)) as sleep_mock:
        async with provider.stream_client_with_telemetry(_make_request()) as lines:
            chunks = [line async for line in lines]
    assert chunks == ["ok"]
    assert sleep_mock.await_count == 1
    # Retry-After: 2 → exactly 2 seconds, NOT the default schedule.
    assert sleep_mock.await_args_list[0].args[0] == 2.0


@pytest.mark.asyncio
async def test_stream_retries_on_502_and_504():
    """Other transient upstream errors get the same retry treatment."""
    transport = _StatusSequenceTransport(
        [
            (502, {}, b""),
            (504, {}, b""),
            (200, {"content-type": "text/event-stream"}, b"chunk\n"),
        ]
    )
    provider = _make_provider(transport)
    with patch.object(asyncio, "sleep", new=AsyncMock(return_value=None)):
        async with provider.stream_client_with_telemetry(_make_request()) as lines:
            chunks = [line async for line in lines]
    assert chunks == ["chunk"]
    assert transport.calls == 3


@pytest.mark.asyncio
async def test_stream_does_not_retry_on_400():
    """4xx other than 429 must raise immediately (client error, not transient)."""
    transport = _StatusSequenceTransport(
        [
            (400, {"content-type": "application/json"}, b'{"error":"bad request"}'),
        ]
    )
    provider = _make_provider(transport)
    with pytest.raises(httpx.HTTPStatusError):
        async with provider.stream_client_with_telemetry(_make_request()) as lines:
            async for _ in lines:
                pass
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_stream_does_not_retry_on_401_403_404():
    for status in (401, 403, 404):
        transport = _StatusSequenceTransport(
            [(status, {}, b'{"error":"x"}')]
        )
        provider = _make_provider(transport)
        with pytest.raises(httpx.HTTPStatusError):
            async with provider.stream_client_with_telemetry(_make_request()) as lines:
                async for _ in lines:
                    pass
        assert transport.calls == 1


@pytest.mark.asyncio
async def test_stream_raises_after_retry_budget_exhausted():
    """Three consecutive 503s (the litellm cascade) → 4 total attempts then raise."""
    transport = _StatusSequenceTransport(
        [
            (503, {}, b""),
            (503, {}, b""),
            (503, {}, b""),
            (503, {}, b""),
            # Final attempt after all retries used — caller picks this up.
        ]
    )
    provider = _make_provider(transport)
    with patch.object(asyncio, "sleep", new=AsyncMock(return_value=None)):
        with pytest.raises(httpx.HTTPStatusError):
            async with provider.stream_client_with_telemetry(_make_request()) as lines:
                async for _ in lines:
                    pass
    # 4 attempts (1 initial + 3 retries) + 1 final request to synthesize the
    # HTTPStatusError = 5 transport calls.
    assert transport.calls == 5


@pytest.mark.asyncio
async def test_stream_exponential_backoff_progression():
    """Without Retry-After, the schedule must be 1s, 2s, 4s in order."""
    transport = _StatusSequenceTransport(
        [
            (503, {}, b""),
            (503, {}, b""),
            (503, {}, b""),
            (200, {"content-type": "text/event-stream"}, b"final\n"),
        ]
    )
    provider = _make_provider(transport)
    with patch.object(asyncio, "sleep", new=AsyncMock(return_value=None)) as sleep_mock:
        async with provider.stream_client_with_telemetry(_make_request()) as lines:
            chunks = [line async for line in lines]
    assert chunks == ["final"]
    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_stream_malformed_retry_after_falls_back_to_schedule():
    transport = _StatusSequenceTransport(
        [
            (429, {"retry-after": "not-a-number"}, b""),
            (200, {"content-type": "text/event-stream"}, b"ok\n"),
        ]
    )
    provider = _make_provider(transport)
    with patch.object(asyncio, "sleep", new=AsyncMock(return_value=None)) as sleep_mock:
        async with provider.stream_client_with_telemetry(_make_request()) as lines:
            chunks = [line async for line in lines]
    assert chunks == ["ok"]
    # Falls back to the exp schedule, NOT the malformed header.
    assert sleep_mock.await_args_list[0].args[0] == 1.0
