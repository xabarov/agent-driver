"""Typed SDK exceptions for provider/runtime failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from agent_driver.llm.base import provider_request_id
from agent_driver.runtime.errors import RuntimeExecutionError


class AgentDriverSDKError(RuntimeError):
    """Base class for public SDK errors."""


@dataclass(frozen=True, slots=True)
class ProviderErrorDetails:
    """Stable provider error metadata surfaced to SDK callers."""

    provider: str | None
    status_code: int | None
    request_id: str | None
    message: str
    response_body: str | None = None


class ProviderError(AgentDriverSDKError):
    """Provider-neutral SDK exception."""

    def __init__(self, details: ProviderErrorDetails, *, cause: BaseException) -> None:
        super().__init__(details.message)
        self.details = details
        self.__cause__ = cause

    @property
    def status_code(self) -> int | None:
        """HTTP status code when the provider error came from HTTP."""
        return self.details.status_code

    @property
    def request_id(self) -> str | None:
        """Provider request/correlation id when available."""
        return self.details.request_id


class ProviderStatusError(ProviderError):
    """Provider rejected a request with an HTTP error status."""


class ProviderTimeoutError(ProviderError):
    """Provider request timed out."""


class ProviderTransportError(ProviderError):
    """Provider transport failed before a valid response was available."""


def sdk_provider_error_from_runtime(exc: RuntimeExecutionError) -> ProviderError | None:
    """Translate a runtime provider failure into a typed SDK error when possible."""
    cause = _find_provider_cause(exc)
    if cause is None:
        return None
    if isinstance(cause, httpx.HTTPStatusError):
        response = cause.response
        request_id = provider_request_id(response.headers)
        message = _provider_message(response)
        return ProviderStatusError(
            ProviderErrorDetails(
                provider=_provider_from_request(cause.request),
                status_code=response.status_code,
                request_id=request_id,
                message=message,
                response_body=_trim_body(response.text),
            ),
            cause=exc,
        )
    if isinstance(cause, httpx.TimeoutException):
        return ProviderTimeoutError(
            ProviderErrorDetails(
                provider=_provider_from_request(cause.request),
                status_code=None,
                request_id=None,
                message=str(cause) or "provider request timed out",
            ),
            cause=exc,
        )
    if isinstance(cause, httpx.HTTPError):
        return ProviderTransportError(
            ProviderErrorDetails(
                provider=_provider_from_request(cause.request),
                status_code=None,
                request_id=None,
                message=str(cause) or "provider transport error",
            ),
            cause=exc,
        )
    return None


def _find_provider_cause(exc: BaseException) -> httpx.HTTPError | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, httpx.HTTPError):
            return current
        current = current.__cause__
    return None


def _provider_from_request(request: httpx.Request | None) -> str | None:
    if request is None:
        return None
    return str(request.url.host or "") or None


def _provider_message(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    body = _trim_body(response.text)
    if body:
        return body
    return f"provider returned HTTP {response.status_code}"


def _trim_body(text: str | None, *, limit: int = 1000) -> str | None:
    if not text:
        return None
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit]}..."


__all__ = [
    "AgentDriverSDKError",
    "ProviderError",
    "ProviderErrorDetails",
    "ProviderStatusError",
    "ProviderTimeoutError",
    "ProviderTransportError",
    "sdk_provider_error_from_runtime",
]
