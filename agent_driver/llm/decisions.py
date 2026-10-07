"""OpenRouter Decisions transport with deadlines, backoff and circuit recovery."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any

import httpx
from pydantic import ValidationError

from agent_driver.llm.decision_telemetry import DecisionTelemetry, DecisionTiming

from agent_driver.llm.decision_contracts import (
    DecisionAnswer,
    DecisionCircuitOpenError,
    DecisionCriteria,
    DecisionError,
    DecisionProtocolError,
    DecisionProvider,
    DecisionQuestion,
    DecisionQuestionType,
    DecisionRequestError,
    DecisionResponse,
    DecisionTimeoutError,
    DecisionTransportError,
)

_RETRY_STATUSES = {429, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class DecisionClientSettings:
    """Bound the entire call, payload size and repeated provider failures."""

    timeout_s: float = 10.0
    max_attempts: int = 2
    retry_backoff_s: float = 0.25
    failure_limit: int = 3
    cooldown_s: float = 30.0
    max_request_bytes: int = 100_000

    def __post_init__(self) -> None:
        for value in (self.timeout_s, self.cooldown_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("timeouts must be finite and positive")
        if not math.isfinite(self.retry_backoff_s) or self.retry_backoff_s < 0:
            raise ValueError("retry_backoff_s must be finite and non-negative")
        for value in (self.max_attempts, self.failure_limit, self.max_request_bytes):
            if type(value) is not int or value < 1:
                raise ValueError(
                    "attempt, failure and payload limits must be positive integers"
                )


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False

    def enter(self, settings: DecisionClientSettings) -> None:
        if self.opened_at is None:
            return
        if self.probing or monotonic() - self.opened_at < settings.cooldown_s:
            raise DecisionCircuitOpenError()
        self.probing = True

    def failed(self, settings: DecisionClientSettings) -> None:
        self.failures += 1
        if self.failures >= settings.failure_limit:
            self.opened_at = monotonic()

    def succeeded(self) -> None:
        self.failures = 0
        self.opened_at = None


def _retry_delay(response: httpx.Response, fallback: float) -> float:
    raw = response.headers.get("retry-after")
    if raw:
        try:
            seconds = float(raw)
        except ValueError:
            try:
                date = parsedate_to_datetime(raw)
                seconds = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return fallback
        if math.isfinite(seconds):
            return max(0.0, seconds)
    return fallback


def _parse_response(
    raw: Any,
    questions: Mapping[str, DecisionQuestion],
    *,
    latency_ms: float,
    attempts: int,
    request_id: str | None = None,
) -> DecisionResponse:
    try:
        # Parse only the documented answer fields; extra fields fail closed.
        answers = {}
        for key, value in raw["answers"].items():
            payload = dict(value)
            # A few Decisions API builds return a score that is rounded or
            # independently sampled from the probability distribution. The
            # typed contract stores the mathematically expected score; derive it
            # at the transport boundary so the runtime can still apply its
            # confidence floor instead of treating an otherwise well-formed
            # decision as a protocol failure.
            if (
                payload.get("type") == "score"
                and isinstance(payload.get("probabilities"), dict)
                and isinstance(payload.get("legend"), dict)
            ):
                probabilities = payload["probabilities"]
                try:
                    expected = sum(
                        int(label) * float(probability)
                        for label, probability in probabilities.items()
                    )
                except (TypeError, ValueError):
                    expected = None
                if expected is not None:
                    reported = payload.get("score")
                    if not isinstance(reported, (int, float)) or abs(reported - expected) > 0.02:
                        payload["score"] = expected
            answers[key] = DecisionAnswer(question_id=key, **payload)
        usage = raw["usage"]
        model = raw["model"]
        version = raw.get("model_version")
        if version is None and re.search(r"-\d{8}$", model):
            version = model
        result = DecisionResponse(
            model=model,
            model_version=version,
            request_id=request_id or raw.get("request_id") or raw.get("id"),
            provider=raw.get("provider"),
            answers=answers,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=usage["cost"],
            latency_ms=latency_ms,
            attempts=attempts,
        )
        result.validate_questions(questions)
        return result
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
        # Provider errors can echo user input; do not attach their body or exception.
        raise DecisionProtocolError() from None


class OpenRouterDecisionClient:
    """Typed adapter for /api/alpha/decisions. Caller owns an injected client.

    Without an injected client, one client is opened per call and closed on all
    paths. Inject a long-lived AsyncClient to share connection pools in production.
    Set reuse_connections=True and close with aclose() (or async with) to own a
    pool. Telemetry includes failed/cancelled calls and bounded phase timings.
    Cancellation propagates; transport errors are not retried because their billing
    outcome is unknown. Only explicit retryable HTTP responses are retried.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "typesafe/jev-1.13",
        base_url: str = "https://openrouter.ai/api",
        settings: DecisionClientSettings | None = None,
        client: httpx.AsyncClient | None = None,
        reuse_connections: bool = False,
        telemetry: DecisionTelemetry | None = None,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ValueError("api_key and model are required")
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self._api_key = api_key
        self.model = model
        self._url = f"{base}/alpha/decisions"
        self._settings = settings or DecisionClientSettings()
        self._client = client
        self._owns_client = client is None
        self._reuse_connections = reuse_connections
        self._closed = False
        self.telemetry = telemetry or DecisionTelemetry()
        self._circuit = _Circuit()

    async def aclose(self) -> None:
        """Close an owned pool. An injected HTTP client remains caller-owned."""
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def decide(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str | None = None,
    ) -> DecisionResponse:
        """Return complete validated answers, or a safe typed failure."""
        if not questions or any(not isinstance(k, str) or not k for k in questions):
            raise ValueError("named questions are required")
        # Snapshot caller-owned containers before the first await.
        questions = {
            k: DecisionQuestion.model_validate(v.model_dump())
            for k, v in questions.items()
        }
        try:
            body = json.dumps(
                {
                    "model": model or self.model,
                    "state": state,
                    "questions": {k: v.to_payload() for k, v in questions.items()},
                },
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ValueError(
                "decision request must contain finite JSON values"
            ) from None
        if len(body) > self._settings.max_request_bytes:
            raise ValueError("decision request exceeds the configured byte limit")
        if self._closed:
            raise RuntimeError("decision client is closed")
        timing = DecisionTiming()
        result = None
        outcome = "internal_error"
        entered = False
        try:
            self._circuit.enter(self._settings)
            entered = True
            async with asyncio.timeout(self._settings.timeout_s):
                if self._client is None and self._reuse_connections:
                    setup = monotonic()
                    self._client = httpx.AsyncClient()
                    timing.phases["client_setup_ms"] += (monotonic() - setup) * 1000
                if self._client is not None:
                    result = await self._send(self._client, body, questions, timing)
                else:
                    setup = monotonic()
                    async with httpx.AsyncClient() as client:
                        timing.phases["client_setup_ms"] += (monotonic() - setup) * 1000
                        result = await self._send(client, body, questions, timing)
        except (TimeoutError, httpx.TimeoutException):
            outcome = "timeout"
            self._circuit.failed(self._settings)
            raise DecisionTimeoutError() from None
        except httpx.HTTPError:
            outcome = "transport_error"
            self._circuit.failed(self._settings)
            raise DecisionTransportError() from None
        except DecisionError as exc:
            outcome = exc.code
            if entered:
                self._circuit.failed(self._settings)
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        else:
            outcome = "success"
            self._circuit.succeeded()
            return result
        finally:
            if entered:
                self._circuit.probing = False
            self.telemetry.record(
                timing=timing, outcome=outcome,
                cost_usd=result.cost_usd if result else None,
                model=result.model if result else None,
                model_version=result.model_version if result else None,
            )

    async def _send(
        self,
        client: httpx.AsyncClient,
        body: bytes,
        questions: Mapping[str, DecisionQuestion],
        timing: DecisionTiming,
    ) -> DecisionResponse:
        for attempt in range(1, self._settings.max_attempts + 1):
            timing.attempts = attempt
            http_started = monotonic()
            try:
                response = await client.post(
                    self._url, content=body, follow_redirects=False,
                    headers={"Authorization": f"Bearer {self._api_key}",
                             "Content-Type": "application/json"},
                    timeout=self._settings.timeout_s,
                    extensions={"trace": timing.trace},
                )
                timing.status_code = response.status_code
            finally:
                timing.phases["http_ms"] += (monotonic() - http_started) * 1000
            if (
                response.status_code in _RETRY_STATUSES
                and attempt < self._settings.max_attempts
            ):
                delay = _retry_delay(
                    response, self._settings.retry_backoff_s * 2 ** (attempt - 1)
                )
                remaining = self._settings.timeout_s - (monotonic() - timing.started)
                if delay < remaining:
                    retry_started = monotonic()
                    try:
                        await asyncio.sleep(delay)
                    finally:
                        timing.phases["retry_wait_ms"] += (monotonic() - retry_started) * 1000
                    continue
            if not 200 <= response.status_code < 300:
                raise DecisionRequestError(status_code=response.status_code)
            decode_started = monotonic()
            try:
                try:
                    raw = response.json()
                except ValueError:
                    raise DecisionProtocolError() from None
                return _parse_response(
                    raw, questions,
                    latency_ms=(monotonic() - timing.started) * 1000,
                    attempts=attempt,
                    request_id=response.headers.get("x-request-id"),
                )
            finally:
                timing.phases["decode_validate_ms"] += (monotonic() - decode_started) * 1000
        raise DecisionTransportError()  # all iterations return, raise or retry


class FakeDecisionProvider:
    """Replay detached typed responses/errors offline; stores no input state."""

    def __init__(self, responses: Sequence[DecisionResponse | DecisionError]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def decide(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        model: str | None = None,
    ) -> DecisionResponse:
        """Consume one fixture, validating it against the caller's questions."""
        if self.calls >= len(self._responses):
            raise DecisionProtocolError()
        result = self._responses[self.calls]
        self.calls += 1
        if isinstance(result, DecisionError):
            raise result
        result = result.model_copy(deep=True)
        result.validate_questions(questions)
        return result


__all__ = [
    "DecisionAnswer",
    "DecisionCircuitOpenError",
    "DecisionClientSettings",
    "DecisionCriteria",
    "DecisionError",
    "DecisionProtocolError",
    "DecisionProvider",
    "DecisionQuestion",
    "DecisionQuestionType",
    "DecisionRequestError",
    "DecisionResponse",
    "DecisionTimeoutError",
    "DecisionTransportError",
    "FakeDecisionProvider",
    "OpenRouterDecisionClient",
]
