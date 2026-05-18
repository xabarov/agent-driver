"""Shared provider base helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any, TypeVar

import httpx

from agent_driver.llm.contracts import LlmProviderKind, ProviderStatus

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StreamRequest:
    """HTTP stream request parameters used by provider adapters."""

    timeout_s: float
    method: str
    url: str
    handled_exceptions: tuple[type[BaseException], ...]
    headers: dict[str, str] | None = None
    json: dict[str, Any] | None = None


class ProviderBase:
    """Common provider telemetry helpers and status management."""

    def __init__(
        self,
        *,
        name: str,
        kind: LlmProviderKind,
        configured: bool,
        cost_per_1k_tokens: float = 0.0,
    ) -> None:
        self._name = name
        self._status = ProviderStatus(
            provider_name=name,
            provider_kind=kind,
            healthy=True,
            configured=configured,
            latency_ms=None,
            avg_latency_ms=None,
            request_count=0,
            error_count=0,
            cost_per_1k_tokens=cost_per_1k_tokens,
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
        """Open HTTP stream with telemetry and yield iterator over text lines."""
        async with self.stream_with_telemetry(
            handled_exceptions=request.handled_exceptions
        ):
            async with httpx.AsyncClient(timeout=request.timeout_s) as client:
                async with client.stream(
                    request.method,
                    request.url,
                    headers=request.headers,
                    json=request.json,
                ) as response:
                    response.raise_for_status()
                    yield response.aiter_lines()

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
