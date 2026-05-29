"""Offline tests for health-aware router behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
    RouterStrategy,
)
from agent_driver.llm.providers import LlmProvider
from agent_driver.llm.router import HealthAwareRouter


class _StubProvider:
    """Minimal async provider stub for router tests."""

    @dataclass(slots=True)
    class Config:
        """Factory config to keep initializer small and explicit."""

        name: str
        healthy: bool
        configured: bool = True
        avg_latency_ms: float = 100.0
        cost_per_1k_tokens: float = 0.0
        fail_complete: bool = False
        response_text: str = "ok"

    def __init__(self, config: Config) -> None:
        self._name = config.name
        self._fail_complete = config.fail_complete
        self._response_text = config.response_text
        self._status = ProviderStatus(
            provider_name=config.name,
            provider_kind=LlmProviderKind.FAKE,
            healthy=config.healthy,
            configured=config.configured,
            avg_latency_ms=config.avg_latency_ms,
            cost_per_1k_tokens=config.cost_per_1k_tokens,
            request_count=0,
            error_count=0,
        )

    @property
    def name(self) -> str:
        """Stable provider name."""
        return self._name

    @property
    def status(self) -> ProviderStatus:
        """Mutable provider status."""
        return self._status

    async def healthcheck(self) -> ProviderStatus:
        """Return static health status."""
        return self._status

    async def complete(self, request: LlmRequest):  # noqa: ANN001
        """Return deterministic response or raise simulated failure."""
        if self._fail_complete:
            raise RuntimeError(f"{self._name} failed")
        return LlmResponse(
            message=ChatMessage(
                role="assistant", content=f"{self._name}:{self._response_text}"
            ),
            finish_reason=LlmFinishReason.STOP,
            provider=self._name,
            model=request.model or "stub",
        )

    async def stream(self, request: LlmRequest):  # noqa: ANN001
        """Not required for router tests."""
        _ = request
        raise NotImplementedError


class _StreamStubProvider(_StubProvider):
    """Provider stub with controllable stream behavior."""

    def __init__(
        self,
        config: _StubProvider.Config,
        *,
        startup_fail: bool = False,
        fail_after_first_chunk: bool = False,
        fail_with_value_error: bool = False,
    ) -> None:
        super().__init__(config)
        self._startup_fail = startup_fail
        self._fail_after_first_chunk = fail_after_first_chunk
        self._fail_with_value_error = fail_with_value_error

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        """Yield deterministic stream with optional startup/midstream failures."""
        _ = request
        if self._startup_fail:
            if self._fail_with_value_error:
                raise ValueError(f"{self.name} startup failed")
            raise RuntimeError(f"{self.name} startup failed")
        yield LlmStreamEvent(event="delta", delta_text=f"{self.name}-chunk1")
        if self._fail_after_first_chunk:
            if self._fail_with_value_error:
                raise ValueError(f"{self.name} midstream failed")
            raise RuntimeError(f"{self.name} midstream failed")
        yield LlmStreamEvent(
            event="done",
            delta_text="",
            finish_reason=LlmFinishReason.STOP,
        )


@pytest.mark.asyncio
async def test_router_selects_healthy_provider() -> None:
    """Router should select available healthy/configured provider."""
    healthy = _StubProvider(
        _StubProvider.Config(name="healthy", healthy=True, avg_latency_ms=10.0)
    )
    unhealthy = _StubProvider(
        _StubProvider.Config(name="unhealthy", healthy=False, avg_latency_ms=1.0)
    )
    router = HealthAwareRouter(
        providers=[unhealthy, healthy], strategy=RouterStrategy.LATENCY
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    response = await router.complete(request)
    assert response.provider == "healthy"


@pytest.mark.asyncio
async def test_router_fallback_skips_failed_provider() -> None:
    """Router should fallback to next candidate when first provider fails."""
    failing = _StubProvider(
        _StubProvider.Config(
            name="failing",
            healthy=True,
            avg_latency_ms=1.0,
            fail_complete=True,
        )
    )
    backup = _StubProvider(
        _StubProvider.Config(name="backup", healthy=True, avg_latency_ms=5.0)
    )
    router = HealthAwareRouter(
        providers=[failing, backup], strategy=RouterStrategy.LATENCY
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    response = await router.complete(request)

    assert response.provider == "backup"
    assert failing.status.error_count == 0
    assert failing.status.request_count == 0
    assert failing.status.healthy is False


@pytest.mark.asyncio
async def test_router_record_result_updates_telemetry() -> None:
    """Router telemetry updater should track request and latency metrics."""
    provider = _StubProvider(
        _StubProvider.Config(name="p1", healthy=True, avg_latency_ms=50.0)
    )
    router = HealthAwareRouter(providers=[provider], strategy=RouterStrategy.BALANCED)
    router.record_result(provider, success=True, elapsed_ms=100.0)

    assert provider.status.request_count == 0
    assert provider.status.latency_ms is not None
    assert provider.status.avg_latency_ms is not None
    assert provider.status.avg_latency_ms > 50.0


def _assert_protocol(provider: LlmProvider) -> None:
    """Type-check helper ensuring stub matches provider protocol."""
    assert provider is not None


def test_stub_provider_satisfies_protocol() -> None:
    """Smoke-check stub provider against provider protocol type."""
    _assert_protocol(_StubProvider(_StubProvider.Config(name="typed", healthy=True)))


@pytest.mark.asyncio
async def test_router_stream_success_emits_chunks() -> None:
    """Router stream should relay chunks from selected healthy provider."""
    primary = _StreamStubProvider(
        _StubProvider.Config(name="stream-primary", healthy=True, avg_latency_ms=1.0)
    )
    router = HealthAwareRouter(providers=[primary], strategy=RouterStrategy.LATENCY)
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    chunks = [chunk async for chunk in router.stream(request)]

    assert [chunk.event for chunk in chunks] == ["delta", "done"]
    assert chunks[0].delta_text == "stream-primary-chunk1"


@pytest.mark.asyncio
async def test_router_stream_startup_fallback_to_backup() -> None:
    """Router should fallback if first provider stream fails before first chunk."""
    failing = _StreamStubProvider(
        _StubProvider.Config(name="stream-failing", healthy=True, avg_latency_ms=1.0),
        startup_fail=True,
    )
    backup = _StreamStubProvider(
        _StubProvider.Config(name="stream-backup", healthy=True, avg_latency_ms=5.0)
    )
    router = HealthAwareRouter(
        providers=[failing, backup], strategy=RouterStrategy.LATENCY
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    chunks = [chunk async for chunk in router.stream(request)]

    assert chunks[0].delta_text == "stream-backup-chunk1"


@pytest.mark.asyncio
async def test_router_stream_does_not_fallback_after_first_chunk() -> None:
    """Router must not fallback after stream already emitted at least one chunk."""
    failing = _StreamStubProvider(
        _StubProvider.Config(name="stream-failing", healthy=True, avg_latency_ms=1.0),
        fail_after_first_chunk=True,
    )
    backup = _StreamStubProvider(
        _StubProvider.Config(name="stream-backup", healthy=True, avg_latency_ms=5.0)
    )
    router = HealthAwareRouter(
        providers=[failing, backup], strategy=RouterStrategy.LATENCY
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    with pytest.raises(RuntimeError):
        _ = [chunk async for chunk in router.stream(request)]


@pytest.mark.asyncio
async def test_router_stream_startup_value_error_fallback_to_backup() -> None:
    """Router should fallback when startup throws ValueError before first chunk."""
    failing = _StreamStubProvider(
        _StubProvider.Config(name="stream-failing", healthy=True, avg_latency_ms=1.0),
        startup_fail=True,
        fail_with_value_error=True,
    )
    backup = _StreamStubProvider(
        _StubProvider.Config(name="stream-backup", healthy=True, avg_latency_ms=5.0)
    )
    router = HealthAwareRouter(
        providers=[failing, backup], strategy=RouterStrategy.LATENCY
    )
    request = LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")
    chunks = [chunk async for chunk in router.stream(request)]

    assert chunks[0].delta_text == "stream-backup-chunk1"
