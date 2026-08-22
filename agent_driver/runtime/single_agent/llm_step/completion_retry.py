"""Pure transient-retry classification + stream-failure diagnostics for the LLM
completion path (extracted from completion.py).

Leaf module: predicates over exceptions/context/request (transient transport and
provider-status detection, retry-delay computation, stream-usefulness checks, the
exception-chain + retry-diagnostics builders, and the prior-assistant-content
probe). No host, no back-edge into the completion flow — the import stays one-way
(completion -> completion_retry).
"""

from __future__ import annotations
from typing import Any
import httpx
from agent_driver.contracts.enums import ChatRole
from agent_driver.llm.error_classifier import (
    ProviderTransportError,
)
from agent_driver.llm.retry_directives import (
    rate_limit_reset_seconds,
)
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmStreamIdleTimeout,
)
from agent_driver.runtime.single_agent.types import (
    RunContext,
)

_TRANSIENT_PROVIDER_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
# Non-timeout transport failures worth a bounded blind retry: httpx.NetworkError
# covers ConnectError/ReadError/WriteError/CloseError; RemoteProtocolError is the
# "server disconnected" class. LocalProtocolError (a client-side body bug) is
# intentionally excluded — retrying it never helps.
_TRANSIENT_HTTPX_TRANSPORT = (httpx.NetworkError, httpx.RemoteProtocolError)
# Floor for adopting a prior assistant message as the forced-final answer. Mirrors the
# partial-final stream recovery floor (200 chars): shorter texts are usually loop
# narration («ищу дальше…»), not an answer worth finalizing with.
_PRIOR_TURN_MIN_CHARS = 200


def _is_transient_transport_error(exc: BaseException) -> bool:
    """Whether ``exc`` is a transient network/transport hiccup worth retrying."""
    if isinstance(exc, _TRANSIENT_HTTPX_TRANSPORT):
        return True
    # ``ProviderTransportError`` (a RuntimeError subclass) wraps the httpx
    # transport errors the provider raised while opening/reading the stream.
    return isinstance(exc, ProviderTransportError)


def _is_transient_provider_status(exc: httpx.HTTPStatusError) -> bool:
    """Whether this provider failure is worth a bounded blind retry.

    Overload/rate-limit/gateway hiccups (the OpenRouter «LLM completion
    failed» class seen live) usually clear within seconds; anything else is
    treated as deterministic and surfaces immediately.
    """
    return exc.response.status_code in _TRANSIENT_PROVIDER_STATUSES


def _transient_retry_delay(exc: httpx.HTTPStatusError, attempt: int) -> float:
    """Backoff for a transient provider error, honoring server wait directives.

    Waits the longer of the exponential base, a ``Retry-After``, and any
    ``*ratelimit*reset*`` header (F3), capped at 10s so a bounded retry stays
    bounded — the loop re-reads the header on the next attempt.
    """
    delay = 2.0 * (attempt + 1)
    retry_after = exc.response.headers.get("retry-after")
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    reset = rate_limit_reset_seconds(exc.response.headers)
    if reset is not None:
        delay = max(delay, reset)
    return min(delay, 10.0)


def _should_retry_stream_failure_without_streaming(
    context: RunContext,
    request: Any,
    attempt: int,
) -> bool:
    if attempt != 0 or not getattr(request, "stream", False):
        return False
    if context.metadata.get("provider_stream_non_stream_fallback") is True:
        return False
    if _stream_has_useful_output(context):
        return False
    return callable(getattr(request, "model_copy", None))


def _stream_has_useful_output(context: RunContext) -> bool:
    # Empty provider heartbeat events are not user-visible output, but they prove
    # the stream opened. Keep those failures diagnosable instead of silently
    # converting a malformed stream into a non-stream success.
    events_seen = context.metadata.get("assistant_stream_events_seen")
    if isinstance(events_seen, int) and events_seen > 0:
        return True
    if context.metadata.get("assistant_stream_tool_intent_seen") is True:
        return True
    content = context.metadata.get("assistant_stream_content")
    if isinstance(content, str) and content:
        return True
    for key in (
        "assistant_stream_token_chunks_seen",
        "assistant_stream_reasoning_chunks_seen",
    ):
        value = context.metadata.get(key)
        if isinstance(value, int) and value > 0:
            return True
    return False


def _stream_failure_retry_diagnostics(
    context: RunContext,
    request: Any,
    exc: BaseException,
    *,
    provider_name: str,
    transition_reason: str,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "transition_reason": transition_reason,
        "provider": provider_name,
        "model": getattr(request, "model", None) or "stream-model",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "stream_events_seen": int(
            context.metadata.get("assistant_stream_events_seen") or 0
        ),
        "token_chunks_seen": int(
            context.metadata.get("assistant_stream_token_chunks_seen") or 0
        ),
        "reasoning_chunks_seen": int(
            context.metadata.get("assistant_stream_reasoning_chunks_seen") or 0
        ),
        "assistant_stream_started": context.metadata.get("assistant_stream_started")
        is True,
        "assistant_stream_completed": context.metadata.get("assistant_stream_completed")
        is True,
        "assistant_stream_tool_intent_seen": context.metadata.get(
            "assistant_stream_tool_intent_seen"
        )
        is True,
    }
    chain = _exception_chain(exc)
    if chain:
        diagnostics["exception_chain"] = chain
    if isinstance(exc, LlmStreamIdleTimeout):
        diagnostics["idle_timeout_seconds"] = exc.idle_timeout_seconds
        diagnostics["idle_timeout_emitted_chunks"] = exc.emitted_chunks
    return diagnostics


def _exception_chain(exc: BaseException) -> list[dict[str, str]]:
    chain: list[dict[str, str]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "message": str(current),
            }
        )
        current = current.__cause__ or current.__context__
    return chain


def _longest_prior_assistant_content(request: Any) -> str | None:
    """Longest substantive assistant text already present in this run's history."""
    messages = getattr(request, "messages", None) or []
    best = ""
    for message in messages:
        role = getattr(message, "role", None)
        if role != ChatRole.ASSISTANT:
            continue
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict) and (
            metadata.get("tool_calls") or metadata.get("folded_tool_calls")
        ):
            continue
        content = (getattr(message, "content", "") or "").strip()
        if len(content) > len(best):
            best = content
    return best if len(best) >= _PRIOR_TURN_MIN_CHARS else None
