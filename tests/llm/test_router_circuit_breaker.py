"""Per-provider circuit breaker (F2): sticky open/half-open/cooldown state."""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    ProviderStatus,
)
from agent_driver.llm.router import HealthAwareRouter
from agent_driver.sdk.errors import ProviderErrorDetails, ProviderStatusError


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _Provider:
    """Minimal provider; ``fail`` toggles whether ``complete`` raises 503."""

    def __init__(self, name: str, *, fail: bool = False, latency_ms: float = 10.0) -> None:
        self._name = name
        self.fail = fail
        self.calls = 0
        self._status = ProviderStatus(
            provider_name=name,
            provider_kind=LlmProviderKind.FAKE,
            healthy=True,
            configured=True,
            avg_latency_ms=latency_ms,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> ProviderStatus:
        return self._status

    async def healthcheck(self) -> ProviderStatus:
        # A passing healthcheck deliberately keeps status.healthy True — the whole
        # point of the breaker is to skip the provider anyway.
        self._status.healthy = True
        return self._status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.fail:
            raise ProviderStatusError(
                ProviderErrorDetails(
                    provider=self._name,
                    status_code=503,
                    request_id="req",
                    message="server error",
                ),
                cause=RuntimeError("boom"),
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            provider=self._name,
            model=request.model or "stub",
        )


def _router(providers: list[_Provider], clock: _Clock, **kw: object) -> HealthAwareRouter:
    return HealthAwareRouter(providers, now=clock, **kw)  # type: ignore[arg-type]


def _fail(router: HealthAwareRouter, provider: _Provider, n: int = 1) -> None:
    for _ in range(n):
        router.record_result(provider, success=False, elapsed_ms=5.0, mark_unhealthy=True)


# --------------------------------------------------------------------------- #
# State-machine unit tests                                                    #
# --------------------------------------------------------------------------- #


def test_opens_after_threshold_consecutive_failures() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_failure_threshold=3)
    _fail(router, p, 2)
    assert not router._breaker_open(p)  # below threshold → closed
    _fail(router, p, 1)
    assert router._breaker_open(p)  # 3rd failure opens the circuit


def test_success_resets_the_failure_streak() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_failure_threshold=3)
    _fail(router, p, 2)
    router.record_result(p, success=True, elapsed_ms=5.0)
    _fail(router, p, 2)
    assert not router._breaker_open(p)  # streak reset by the success


def test_non_unhealthy_failure_never_opens() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_failure_threshold=3)
    for _ in range(5):
        router.record_result(p, success=False, elapsed_ms=5.0, mark_unhealthy=False)
    assert not router._breaker_open(p)  # bad request, not a down provider


def test_cooldown_then_half_open_probe() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_failure_threshold=3, circuit_cooldown_seconds=30.0)
    _fail(router, p, 3)
    assert router._breaker_open(p)
    clock.advance(29.0)
    assert router._breaker_open(p)  # still cooling down
    clock.advance(2.0)  # now past 30s
    assert not router._breaker_open(p)  # half-open: one probe allowed
    assert router._breaker["a"].half_open is True


def test_probe_success_closes_the_circuit() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_failure_threshold=3, circuit_cooldown_seconds=30.0)
    _fail(router, p, 3)
    clock.advance(31.0)
    assert not router._breaker_open(p)  # → half-open
    router.record_result(p, success=True, elapsed_ms=5.0)
    assert router._breaker["a"].opened_at is None
    assert router._breaker["a"].open_count == 0


def test_probe_failure_reopens_with_escalated_cooldown() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_failure_threshold=3, circuit_cooldown_seconds=30.0)
    _fail(router, p, 3)
    clock.advance(31.0)
    assert not router._breaker_open(p)  # → half-open
    _fail(router, p, 1)  # probe fails → re-open, cooldown doubles to 60s
    assert router._breaker["a"].open_count == 2
    clock.advance(31.0)  # 31s < 60s
    assert router._breaker_open(p)  # still open under the escalated cooldown
    clock.advance(30.0)  # now past 60s
    assert not router._breaker_open(p)


def test_disabled_breaker_never_opens() -> None:
    clock = _Clock()
    p = _Provider("a")
    router = _router([p], clock, circuit_breaker_enabled=False, circuit_failure_threshold=1)
    _fail(router, p, 5)
    assert not router._breaker_open(p)


# --------------------------------------------------------------------------- #
# Integration: an open provider is skipped during selection                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_open_provider_excluded_from_selection() -> None:
    clock = _Clock()
    # bad is much faster, so it's always ranked first (and retried) until its
    # circuit opens — isolating the breaker's exclusion from latency scoring.
    bad = _Provider("bad", fail=True, latency_ms=1.0)
    good = _Provider("good", latency_ms=1000.0)
    router = _router([bad, good], clock, circuit_failure_threshold=3)

    # Three completions: each tries bad (fails) then falls over to good.
    for _ in range(3):
        resp = await router.complete(LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m"))
        assert resp.message.content == "ok"
    assert router._breaker_open(bad)  # bad's circuit opened after 3 failures

    good_calls_before = good.calls
    bad_calls_before = bad.calls
    # Next call must skip the open provider entirely — bad.complete not invoked.
    await router.complete(LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m"))
    assert bad.calls == bad_calls_before  # circuit open → not even attempted
    assert good.calls == good_calls_before + 1
