"""Semantic classification of provider failures into recovery actions.

Transport-level retries for transient ``429`` / ``502`` / ``503`` / ``504``
responses already live in :mod:`agent_driver.llm.base` (``ProviderBase``).
This module sits one layer up: given an exception raised by a provider, it
decides *why* the request failed and *what the router/runtime should do
about it* — fail fast, back off and rotate, or compress and retry.

The :func:`classify` function is pure and deterministic so the mapping is
exhaustively unit-testable. :class:`HealthAwareRouter` consults it to avoid
treating a deterministic per-request failure (bad credentials, content
policy, oversized prompt) as a transient provider outage worth failing over.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import httpx

from agent_driver.llm.base import provider_request_id
from agent_driver.sdk.errors import (
    ProviderError,
    ProviderStatusError,
    ProviderTimeoutError,
    ProviderTransportError,
)


class ProviderErrorReason(str, Enum):
    """Normalized reason a provider request failed."""

    AUTH = "auth"
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    MODEL_NOT_FOUND = "model_not_found"
    CONTENT_POLICY = "content_policy"
    PROVIDER_POLICY = "provider_policy"
    FORMAT_ERROR = "format_error"
    TRANSPORT = "transport"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    """What the caller should do in response to a classified failure."""

    RETRY_SAME = "retry_same"
    BACKOFF_RETRY = "backoff_retry"
    ROTATE_PROVIDER = "rotate_provider"
    COMPRESS_CONTEXT = "compress_context"
    FAIL_FAST = "fail_fast"


# Reason -> recovery action. Deterministic per-request failures fail fast;
# context overflow is special-cased so the runtime can compress and retry
# instead of pointlessly rotating to another provider that will also reject
# the oversized prompt.
_REASON_ACTION: dict[ProviderErrorReason, RecoveryAction] = {
    ProviderErrorReason.AUTH: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.BILLING: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.RATE_LIMIT: RecoveryAction.BACKOFF_RETRY,
    ProviderErrorReason.OVERLOADED: RecoveryAction.BACKOFF_RETRY,
    ProviderErrorReason.SERVER_ERROR: RecoveryAction.ROTATE_PROVIDER,
    ProviderErrorReason.TIMEOUT: RecoveryAction.ROTATE_PROVIDER,
    ProviderErrorReason.CONTEXT_OVERFLOW: RecoveryAction.COMPRESS_CONTEXT,
    ProviderErrorReason.PAYLOAD_TOO_LARGE: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.MODEL_NOT_FOUND: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.CONTENT_POLICY: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.PROVIDER_POLICY: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.FORMAT_ERROR: RecoveryAction.FAIL_FAST,
    ProviderErrorReason.TRANSPORT: RecoveryAction.ROTATE_PROVIDER,
    ProviderErrorReason.UNKNOWN: RecoveryAction.ROTATE_PROVIDER,
}

# Reasons that indicate the provider itself is unhealthy / transiently down,
# as opposed to a request- or config-level problem the same provider would
# reject again. Only these should drop a provider out of router rotation.
_UNHEALTHY_REASONS: frozenset[ProviderErrorReason] = frozenset(
    {
        ProviderErrorReason.RATE_LIMIT,
        ProviderErrorReason.OVERLOADED,
        ProviderErrorReason.SERVER_ERROR,
        ProviderErrorReason.TIMEOUT,
        ProviderErrorReason.TRANSPORT,
        ProviderErrorReason.UNKNOWN,
    }
)

_OVERFLOW_MARKERS: tuple[str, ...] = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "reduce the length",
    "maximum number of tokens",
    "prompt is too long",
)
_CONTENT_POLICY_MARKERS: tuple[str, ...] = (
    "content policy",
    "content_policy",
    "safety",
    "flagged",
    "moderation",
    "violat",
    "responsible ai",
)
_PAYLOAD_MARKERS: tuple[str, ...] = (
    "request entity too large",
    "payload too large",
    "request too large",
    "body too large",
)
_MODEL_MARKERS: tuple[str, ...] = (
    "model",
    "deployment",
)


@dataclass(frozen=True, slots=True)
class ClassifiedError:
    """A provider failure resolved to a reason and a recovery action."""

    reason: ProviderErrorReason
    action: RecoveryAction
    provider: str | None = None
    status_code: int | None = None
    request_id: str | None = None
    message: str = ""

    @property
    def marks_unhealthy(self) -> bool:
        """Whether this failure should drop the provider out of rotation."""
        return self.reason in _UNHEALTHY_REASONS

    @property
    def is_fatal(self) -> bool:
        """Whether the caller must stop rather than rotate to another provider.

        Both ``FAIL_FAST`` (deterministic per-request) and
        ``COMPRESS_CONTEXT`` (needs the runtime to shrink the prompt) are
        fatal *to provider rotation*: trying a sibling provider will not help.
        """
        return self.action in (
            RecoveryAction.FAIL_FAST,
            RecoveryAction.COMPRESS_CONTEXT,
        )

    @property
    def retryable(self) -> bool:
        """Whether retrying the same provider (after backoff) can succeed."""
        return self.action in (RecoveryAction.RETRY_SAME, RecoveryAction.BACKOFF_RETRY)


def _contains(haystack: str, markers: tuple[str, ...]) -> bool:
    lowered = haystack.lower()
    return any(marker in lowered for marker in markers)


# Status codes whose reason is unambiguous from the code alone. Codes that
# need message heuristics to disambiguate (400 / 403 / 422) are handled by
# ``_classify_ambiguous_status`` instead.
_EXACT_STATUS: dict[int, ProviderErrorReason] = {
    401: ProviderErrorReason.AUTH,
    402: ProviderErrorReason.BILLING,
    404: ProviderErrorReason.MODEL_NOT_FOUND,
    408: ProviderErrorReason.TIMEOUT,
    413: ProviderErrorReason.PAYLOAD_TOO_LARGE,
    429: ProviderErrorReason.RATE_LIMIT,
    529: ProviderErrorReason.OVERLOADED,  # Anthropic "overloaded"
    502: ProviderErrorReason.OVERLOADED,
    503: ProviderErrorReason.OVERLOADED,
    504: ProviderErrorReason.OVERLOADED,
}


# Ordered marker → reason rules for a 400 (most specific first). The first
# matching marker set wins; nothing matching falls through to a format error.
_BAD_REQUEST_RULES: tuple[tuple[tuple[str, ...], ProviderErrorReason], ...] = (
    (_OVERFLOW_MARKERS, ProviderErrorReason.CONTEXT_OVERFLOW),
    (_PAYLOAD_MARKERS, ProviderErrorReason.PAYLOAD_TOO_LARGE),
    (_CONTENT_POLICY_MARKERS, ProviderErrorReason.CONTENT_POLICY),
    (_MODEL_MARKERS, ProviderErrorReason.MODEL_NOT_FOUND),
)


def _classify_ambiguous_status(  # pylint: disable=too-many-return-statements
    status_code: int, message: str
) -> ProviderErrorReason | None:
    """Disambiguate bad-request codes (400 / 403 / 422) by message hints."""
    if status_code == 403:
        # 403 overloads auth scope and content policy depending on provider.
        if _contains(message, _CONTENT_POLICY_MARKERS):
            return ProviderErrorReason.CONTENT_POLICY
        return ProviderErrorReason.AUTH
    if status_code == 422:
        if _contains(message, _OVERFLOW_MARKERS):
            return ProviderErrorReason.CONTEXT_OVERFLOW
        return ProviderErrorReason.FORMAT_ERROR
    if status_code == 400:
        for markers, reason in _BAD_REQUEST_RULES:
            if _contains(message, markers):
                return reason
        return ProviderErrorReason.FORMAT_ERROR
    return None


def _classify_status(status_code: int, message: str) -> ProviderErrorReason:
    """Map an HTTP status code (plus message hints) to a reason."""
    exact = _EXACT_STATUS.get(status_code)
    if exact is not None:
        return exact
    ambiguous = _classify_ambiguous_status(status_code, message)
    if ambiguous is not None:
        return ambiguous
    if 500 <= status_code < 600:
        return ProviderErrorReason.SERVER_ERROR
    return ProviderErrorReason.UNKNOWN


def _find_httpx_cause(exc: BaseException) -> httpx.HTTPError | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, httpx.HTTPError):
            return current
        current = current.__cause__
    return None


def _make(
    reason: ProviderErrorReason,
    *,
    provider: str | None = None,
    status_code: int | None = None,
    request_id: str | None = None,
    message: str = "",
) -> ClassifiedError:
    return ClassifiedError(
        reason=reason,
        action=_REASON_ACTION[reason],
        provider=provider,
        status_code=status_code,
        request_id=request_id,
        message=message,
    )


def classify(exc: BaseException) -> ClassifiedError:
    """Classify a provider failure into a reason and recovery action.

    Handles the typed SDK provider errors
    (:class:`~agent_driver.sdk.errors.ProviderStatusError` and siblings) as
    well as raw ``httpx`` failures found anywhere on the cause chain. Anything
    unrecognized resolves to ``UNKNOWN`` / ``ROTATE_PROVIDER`` so existing
    fail-over behavior is preserved for generic ``RuntimeError`` failures.
    """
    from_sdk = _classify_sdk_error(exc)
    if from_sdk is not None:
        return from_sdk

    from_httpx = _classify_httpx(_find_httpx_cause(exc))
    if from_httpx is not None:
        return from_httpx

    return _make(ProviderErrorReason.UNKNOWN, message=str(exc))


def _classify_sdk_error(exc: BaseException) -> ClassifiedError | None:
    """Classify a typed SDK provider error, or ``None`` if it is not one."""
    if not isinstance(exc, ProviderError):
        return None
    if isinstance(exc, ProviderStatusError) and exc.status_code is not None:
        reason = _classify_status(exc.status_code, exc.details.message)
    elif isinstance(exc, ProviderTimeoutError):
        reason = ProviderErrorReason.TIMEOUT
    elif isinstance(exc, ProviderTransportError):
        reason = ProviderErrorReason.TRANSPORT
    else:
        reason = ProviderErrorReason.UNKNOWN
    return _make(
        reason,
        provider=exc.details.provider,
        status_code=exc.status_code,
        request_id=exc.request_id,
        message=exc.details.message,
    )


def _classify_httpx(cause: httpx.HTTPError | None) -> ClassifiedError | None:
    """Classify a raw ``httpx`` error found on the exception cause chain."""
    if isinstance(cause, httpx.HTTPStatusError):
        response = cause.response
        message = response.text or ""
        provider = str(cause.request.url.host or "") or None if cause.request else None
        return _make(
            _classify_status(response.status_code, message),
            provider=provider,
            status_code=response.status_code,
            request_id=provider_request_id(response.headers),
            message=message,
        )
    if isinstance(cause, httpx.TimeoutException):
        return _make(ProviderErrorReason.TIMEOUT, message=str(cause))
    if isinstance(cause, httpx.HTTPError):
        return _make(ProviderErrorReason.TRANSPORT, message=str(cause))
    return None


__all__ = [
    "ClassifiedError",
    "ProviderErrorReason",
    "RecoveryAction",
    "classify",
]
