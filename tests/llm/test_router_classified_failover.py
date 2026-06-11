"""Router behavior driven by classified provider failures."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    ProviderStatus,
)
from agent_driver.llm.error_classifier import ProviderErrorReason, RecoveryAction
from agent_driver.llm.router import HealthAwareRouter
from agent_driver.sdk.errors import ProviderErrorDetails, ProviderStatusError


@dataclass(slots=True)
class _Cfg:
    name: str
    fail_status: int | None = None
    fail_message: str = ""
    response_text: str = "ok"


class _TypedFailProvider:
    """Provider stub that raises a typed ProviderStatusError on demand."""

    def __init__(self, cfg: _Cfg) -> None:
        self._cfg = cfg
        self._status = ProviderStatus(
            provider_name=cfg.name,
            provider_kind=LlmProviderKind.FAKE,
            healthy=True,
            configured=True,
            avg_latency_ms=10.0,
        )

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def status(self) -> ProviderStatus:
        return self._status

    async def healthcheck(self) -> ProviderStatus:
        return self._status

    async def complete(self, request: LlmRequest) -> LlmResponse:
        if self._cfg.fail_status is not None:
            raise ProviderStatusError(
                ProviderErrorDetails(
                    provider=self._cfg.name,
                    status_code=self._cfg.fail_status,
                    request_id="req",
                    message=self._cfg.fail_message,
                ),
                cause=RuntimeError("boom"),
            )
        return LlmResponse(
            message=ChatMessage(
                role="assistant", content=f"{self._cfg.name}:{self._cfg.response_text}"
            ),
            provider=self._cfg.name,
            model=request.model or "stub",
        )

    async def stream(self, request: LlmRequest):  # noqa: ANN001
        _ = request
        raise NotImplementedError


def _request() -> LlmRequest:
    return LlmRequest(messages=[ChatMessage(role="user", content="hi")], model="m")


@pytest.mark.asyncio
async def test_auth_failure_does_not_rotate_to_backup() -> None:
    """A 401 is deterministic per request: fail fast without trying the backup."""
    primary = _TypedFailProvider(_Cfg(name="primary", fail_status=401))
    backup = _TypedFailProvider(_Cfg(name="backup"))
    router = HealthAwareRouter(providers=[primary, backup])

    with pytest.raises(ProviderStatusError) as excinfo:
        await router.complete(_request())

    assert excinfo.value.status_code == 401
    # Provider is not dropped from rotation for a request-level failure.
    assert primary.status.healthy is True


@pytest.mark.asyncio
async def test_context_overflow_fails_fast_for_compression() -> None:
    """Context overflow raises rather than rotating, so runtime can compress."""
    primary = _TypedFailProvider(
        _Cfg(
            name="primary",
            fail_status=400,
            fail_message="maximum context length is 8192 tokens",
        )
    )
    backup = _TypedFailProvider(_Cfg(name="backup"))
    router = HealthAwareRouter(providers=[primary, backup])

    with pytest.raises(ProviderStatusError):
        await router.complete(_request())
    assert primary.status.healthy is True


@pytest.mark.asyncio
async def test_server_error_rotates_and_marks_unhealthy() -> None:
    """A 500 marks the provider unhealthy and falls over to the backup."""
    primary = _TypedFailProvider(_Cfg(name="primary", fail_status=500))
    backup = _TypedFailProvider(_Cfg(name="backup", response_text="served"))
    router = HealthAwareRouter(providers=[primary, backup])

    response = await router.complete(_request())

    assert response.provider == "backup"
    assert primary.status.healthy is False


@pytest.mark.asyncio
async def test_exhausted_rotation_attaches_classification() -> None:
    """When all rotate-eligible providers fail, the error carries the reason."""
    primary = _TypedFailProvider(_Cfg(name="primary", fail_status=500))
    backup = _TypedFailProvider(_Cfg(name="backup", fail_status=503))
    router = HealthAwareRouter(providers=[primary, backup])

    with pytest.raises(HealthAwareRouter.ProviderExecutionError) as excinfo:
        await router.complete(_request())

    classified = excinfo.value.classified
    assert classified is not None
    assert classified.reason is ProviderErrorReason.OVERLOADED
    assert classified.action is RecoveryAction.BACKOFF_RETRY
