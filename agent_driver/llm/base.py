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
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-correlation-id",
    "cf-ray",
)


def provider_request_id(headers: httpx.Headers | dict[str, str]) -> str | None:
    """Return a provider request/correlation id from common HTTP headers."""
    for name in _REQUEST_ID_HEADERS:
        value = headers.get(name)
        if value:
            return str(value)
    return None


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
    """Optional HTTP client knobs for provider clients.

    ``transport`` injects a custom httpx transport — primarily for offline
    adapter tests (e.g. ``httpx.MockTransport``).

    .. warning::
       A single ``transport`` **instance** is NOT safe to reuse across clients
       that stream **concurrently**. Every ``httpx.AsyncClient`` closes its
       transport on ``aclose()`` (i.e. when its ``async with`` block exits);
       because providers build a fresh client per call but share this one
       transport, the first client to finish tears down the shared connection
       pool out from under any other in-flight stream — surfacing as
       ``httpx.ReadError`` → ``ProviderTransportError`` on the siblings. So
       ``transport`` is for single-flight use (tests). For the common
       "skip TLS verification" need, use ``verify_ssl=False`` instead: each
       client then builds its **own** default transport (own pool), which is
       concurrency-safe.

    ``verify_ssl`` toggles TLS certificate verification for clients built by
    :meth:`ProviderBase.build_async_client` (set ``False`` for internal
    vLLM/proxy endpoints with self-signed certs). Ignored when ``transport`` is
    set (a custom transport carries its own TLS config).
    """

    transport: httpx.AsyncBaseTransport | None = None
    verify_ssl: bool = True


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
            async with self.build_async_client(timeout_s=request.timeout_s) as client:
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
                        except (
                            httpx.RemoteProtocolError,
                            httpx.ReadError,
                            OSError,
                        ) as exc:
                            # ``RemoteProtocolError`` / ``ReadError`` are httpx's
                            # typed transient stream-open failures. Raw TLS/socket
                            # errors (``ssl.SSLError`` and other ``OSError``
                            # subclasses, e.g. connection reset) can also surface
                            # here on some transports *without* httpx wrapping —
                            # treat them the same and retry within the stream-open
                            # budget rather than letting them fail the whole run.
                            if attempt >= _STREAM_OPEN_RETRIES:
                                # Budget exhausted. httpx errors propagate as-is
                                # (already typed); a raw transport error is
                                # normalized to ``httpx.TransportError`` so upper
                                # layers (``error_classifier`` /
                                # ``stream_with_telemetry``) see a single,
                                # provider-neutral transport type instead of an
                                # untyped ``OSError`` that bypasses classification.
                                if isinstance(exc, httpx.HTTPError):
                                    raise
                                raise httpx.TransportError(
                                    str(exc) or exc.__class__.__name__
                                ) from exc
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
                            # Continue outer status loop after transient retry.
                            break
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
        """Build a fresh async client honoring the HttpClientConfig.

        When a custom ``transport`` is injected (tests) it is used as-is.
        Otherwise each client builds its own default transport with TLS
        verification controlled by ``verify_ssl`` — giving every concurrent
        client an independent connection pool (see the concurrency note on
        :class:`HttpClientConfig`).
        """
        cfg = self._http_client_config
        if cfg.transport is not None:
            return httpx.AsyncClient(timeout=timeout_s, transport=cfg.transport)
        return httpx.AsyncClient(timeout=timeout_s, verify=cfg.verify_ssl)
