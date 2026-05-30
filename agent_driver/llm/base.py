"""Shared provider base helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any, TypeVar

import httpx

from agent_driver.llm.contracts import LlmProviderKind, ProviderStatus

T = TypeVar("T")
_STREAM_OPEN_RETRIES = 1
_STREAM_OPEN_RETRY_BACKOFF_SECONDS = 0.5

# Phase 13 H25 — HTTP status-code-based retry knobs.
# Generic retry loop on top of stream-open retry: when the provider returns
# 429 / 502 / 503 / 504, wait per backoff and retry. Network errors (DNS /
# TLS / reset) are still handled by the stream-open retry above.
_STATUS_RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
_STATUS_RETRY_MAX_ATTEMPTS = 4  # 1 initial + 3 retries
_STATUS_RETRY_BACKOFF_SCHEDULE_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)
_STATUS_RETRY_BACKOFF_CAP_SECONDS = 32.0


def _parse_retry_after(header_value: str | None) -> float | None:
    """Parse the Retry-After header per RFC 7231 §7.1.3.

    Returns the wait duration in seconds, capped at
    ``_STATUS_RETRY_BACKOFF_CAP_SECONDS`` (32s). Returns ``None`` for
    malformed / missing headers; the caller falls back to the exponential
    schedule. Both bare-seconds and HTTP-date forms are accepted; HTTP-
    date is interpreted as "wait until that date" relative to ``now``.
    """
    if not header_value:
        return None
    raw = header_value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
        if seconds < 0:
            return None
        return min(seconds, _STATUS_RETRY_BACKOFF_CAP_SECONDS)
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(raw)
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        if delta < 0:
            return 0.0
        return min(delta, _STATUS_RETRY_BACKOFF_CAP_SECONDS)
    except (TypeError, ValueError):
        return None


def _status_retry_delay(attempt: int, retry_after: float | None) -> float:
    """Compute the wait time before retry ``attempt`` (1-indexed).

    Honors a parsed ``Retry-After`` value when present; otherwise picks
    from the exponential schedule with cap.
    """
    if retry_after is not None:
        return retry_after
    if attempt - 1 < len(_STATUS_RETRY_BACKOFF_SCHEDULE_SECONDS):
        return _STATUS_RETRY_BACKOFF_SCHEDULE_SECONDS[attempt - 1]
    return _STATUS_RETRY_BACKOFF_CAP_SECONDS


@dataclass(frozen=True, slots=True)
class StreamRequest:
    """HTTP stream request parameters used by provider adapters."""

    timeout_s: float
    method: str
    url: str
    handled_exceptions: tuple[type[BaseException], ...]
    headers: dict[str, str] | None = None
    json: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class HttpClientConfig:
    """Optional HTTP client transport hooks for offline adapter tests."""

    transport: httpx.AsyncBaseTransport | None = None


class ProviderBase:
    """Common provider telemetry helpers and status management."""

    @dataclass(frozen=True, slots=True)
    class Config:
        """Shared provider constructor config."""

        name: str
        kind: LlmProviderKind
        configured: bool
        cost_per_1k_tokens: float = 0.0
        http_client_config: HttpClientConfig | None = None

    def __init__(
        self,
        *,
        config: Config,
    ) -> None:
        self._name = config.name
        self._http_client_config = config.http_client_config or HttpClientConfig()
        self._status = ProviderStatus(
            provider_name=config.name,
            provider_kind=config.kind,
            healthy=True,
            configured=config.configured,
            latency_ms=None,
            avg_latency_ms=None,
            request_count=0,
            error_count=0,
            cost_per_1k_tokens=config.cost_per_1k_tokens,
        )

    @property
    def name(self) -> str:
        """Stable provider instance name."""
        return self._name

    @property
    def status(self) -> ProviderStatus:
        """Current provider status snapshot."""
        return self._status

    @staticmethod
    def _started_at() -> float:
        """Return monotonic start timestamp for latency tracking."""
        return monotonic()

    def _mark_success(self, *, started_at: float) -> None:
        """Update latency telemetry after successful request."""
        elapsed_ms = (monotonic() - started_at) * 1000
        self._status.latency_ms = elapsed_ms
        if self._status.avg_latency_ms is None:
            self._status.avg_latency_ms = elapsed_ms
        else:
            self._status.avg_latency_ms = (self._status.avg_latency_ms * 0.7) + (
                elapsed_ms * 0.3
            )
        self._status.healthy = True

    def _mark_attempt(self) -> None:
        """Increment request counter for provider attempt."""
        self._status.request_count += 1

    def _mark_failure(self) -> None:
        """Update status after failed provider attempt."""
        self._status.error_count += 1
        self._status.healthy = False

    async def execute_with_telemetry(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        handled_exceptions: tuple[type[BaseException], ...],
    ) -> T:
        """Run one async operation with request counting and latency/error metrics."""
        self._mark_attempt()
        started = self._started_at()
        try:
            result = await operation()
            self._mark_success(started_at=started)
            return result
        except handled_exceptions:
            self._mark_failure()
            raise

    @asynccontextmanager
    async def stream_client_with_telemetry(
        self,
        request: StreamRequest,
    ) -> AsyncIterator[AsyncIterator[str]]:
        """Open HTTP stream with telemetry and yield iterator over text lines.

        Phase 13 H25 — retry loop semantics:

        * Network errors during stream-open (`RemoteProtocolError`,
          `ReadError`) → ``_STREAM_OPEN_RETRIES`` retries with linear
          backoff (existing behavior).
        * HTTP status codes 429 / 502 / 503 / 504 →
          ``_STATUS_RETRY_MAX_ATTEMPTS`` total attempts with
          exponential backoff (1s / 2s / 4s, cap 32s), honoring
          ``Retry-After`` when the server provides it. This directly
          addresses the ZION recon_v3 ``d9fa88f3`` cascade where
          litellm.c.com returned three consecutive 503s without retry.
        * Other 4xx → no retry, raise immediately.
        * 2xx → yield the stream iterator.
        """
        async with self.stream_with_telemetry(
            handled_exceptions=request.handled_exceptions
        ):
            async with httpx.AsyncClient(
                timeout=request.timeout_s,
                transport=self._http_client_config.transport,
            ) as client:
                status_attempt = 0
                while True:
                    status_attempt += 1
                    for attempt in range(_STREAM_OPEN_RETRIES + 1):
                        stream_context = client.stream(
                            request.method,
                            request.url,
                            headers=request.headers,
                            json=request.json,
                        )
                        try:
                            response = await stream_context.__aenter__()
                        except (httpx.RemoteProtocolError, httpx.ReadError):
                            if attempt >= _STREAM_OPEN_RETRIES:
                                raise
                            await asyncio.sleep(
                                _STREAM_OPEN_RETRY_BACKOFF_SECONDS * (attempt + 1)
                            )
                            continue
                        # Phase 13 H25 — check status before raise_for_status so
                        # we can retry on transient server errors.
                        if response.status_code in _STATUS_RETRY_STATUSES:
                            retry_after = _parse_retry_after(
                                response.headers.get("retry-after")
                            )
                            await stream_context.__aexit__(None, None, None)
                            if status_attempt >= _STATUS_RETRY_MAX_ATTEMPTS:
                                # Out of retries — re-raise as HTTPStatusError
                                # by opening a fresh request and calling
                                # raise_for_status. Cleaner than synthesizing
                                # an httpx exception manually.
                                final_resp = await client.request(
                                    request.method,
                                    request.url,
                                    headers=request.headers,
                                    json=request.json,
                                )
                                final_resp.raise_for_status()
                                # Should not reach here (5xx must raise), but
                                # if the server recovered after we ran out of
                                # retries, yield a synthetic non-stream body.
                                yield iter([])
                                return
                            delay = _status_retry_delay(status_attempt, retry_after)
                            await asyncio.sleep(delay)
                            break  # break the inner stream-open retry loop, continue outer status loop
                        try:
                            if response.status_code >= 400:
                                await response.aread()
                            response.raise_for_status()
                            yield response.aiter_lines()
                            return
                        finally:
                            await stream_context.__aexit__(None, None, None)
                    else:
                        # Inner for-loop completed without break — should not
                        # happen because each attempt either returns, raises,
                        # or hits the network-retry continue. Safety net.
                        return

    @asynccontextmanager
    async def stream_with_telemetry(
        self, *, handled_exceptions: tuple[type[BaseException], ...]
    ) -> AsyncIterator[None]:
        """Track stream request telemetry while yielding chunks progressively."""
        self._mark_attempt()
        started = self._started_at()
        try:
            yield
            self._mark_success(started_at=started)
        except handled_exceptions:
            self._mark_failure()
            raise

    def build_async_client(self, *, timeout_s: float) -> httpx.AsyncClient:
        """Build async client honoring optional transport override."""
        return httpx.AsyncClient(
            timeout=timeout_s,
            transport=self._http_client_config.transport,
        )
