"""Single-provider backoff-retry: with nothing to rotate to, a transient provider-down
failure (timeout / 5xx / transport) is retried on the same provider instead of hard-failing.
"""

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


class _FlakyProvider:
    """Fails its first ``fail_times`` ``complete`` calls with ``fail_status``, then succeeds."""

    def __init__(self, name: str, *, fail_times: int, fail_status: int) -> None:
        self._name = name
        self._fail_times = fail_times
        self._fail_status = fail_status
        self.calls = 0
        self._status = ProviderStatus(
            provider_name=name,
            provider_kind=LlmProviderKind.FAKE,
            healthy=True,
            configured=True,
            avg_latency_ms=10.0,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> ProviderStatus:
        return self._status

    async def healthcheck(self) -> ProviderStatus:
        return self._status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ProviderStatusError(
                ProviderErrorDetails(
                    provider=self._name,
                    status_code=self._fail_status,
                    request_id="req",
                    message="transient",
                ),
                cause=RuntimeError("boom"),
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            provider=self._name,
            model=request.model or "stub",
        )

    async def stream(self, request: LlmRequest):  # noqa: ANN001
        _ = request
        raise NotImplementedError


def _request() -> LlmRequest:
    return LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")


def _router(provider: _FlakyProvider, *, retry_max: int = 2) -> HealthAwareRouter:
    # base_seconds=0 keeps the test fast (no real backoff sleep).
    return HealthAwareRouter(
        providers=[provider],
        single_provider_retry_max=retry_max,
        single_provider_retry_base_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_single_provider_retries_transient_then_succeeds() -> None:
    """A lone provider that blips twice (503) then recovers should still return a response."""
    provider = _FlakyProvider("only", fail_times=2, fail_status=503)
    router = _router(provider, retry_max=2)

    response = await router.complete(_request())

    assert response.provider == "only"
    assert provider.calls == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_single_provider_retry_budget_is_bounded() -> None:
    """When the blip persists past the retry budget, it fails (no infinite retry)."""
    provider = _FlakyProvider("only", fail_times=9, fail_status=503)
    router = _router(provider, retry_max=2)

    with pytest.raises(HealthAwareRouter.ProviderExecutionError):
        await router.complete(_request())

    assert provider.calls == 3  # 1 initial + 2 retries, then give up


@pytest.mark.asyncio
async def test_single_provider_fatal_error_does_not_retry() -> None:
    """A 401 is a deterministic per-request failure — fail fast, never retry."""
    provider = _FlakyProvider("only", fail_times=1, fail_status=401)
    router = _router(provider, retry_max=2)

    with pytest.raises(ProviderStatusError):
        await router.complete(_request())

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_single_provider_retry_can_be_disabled() -> None:
    """retry_max=0 restores the old hard-fail-on-first-error behavior."""
    provider = _FlakyProvider("only", fail_times=1, fail_status=503)
    router = _router(provider, retry_max=0)

    with pytest.raises(HealthAwareRouter.ProviderExecutionError):
        await router.complete(_request())

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_multi_provider_exhaustion_does_not_single_retry() -> None:
    """With more than one provider, exhaustion fails fast — single-provider retry must not engage."""
    primary = _FlakyProvider("primary", fail_times=9, fail_status=500)
    backup = _FlakyProvider("backup", fail_times=9, fail_status=503)
    router = HealthAwareRouter(
        providers=[primary, backup],
        single_provider_retry_base_seconds=0.0,
    )

    with pytest.raises(HealthAwareRouter.ProviderExecutionError):
        await router.complete(_request())

    # Each tried exactly once; no same-provider retry loop.
    assert primary.calls == 1
    assert backup.calls == 1
