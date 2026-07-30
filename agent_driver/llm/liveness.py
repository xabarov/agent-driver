"""Idle-bounded side/aux LLM calls (epic 041 A).

The main agent loop is protected against a wedged provider by ``LlmStreamIdleTimeout``
(streaming) and ``stage_wait_heartbeat`` (epic 025). Side/aux calls — compaction,
structured extraction, suggestions, graders — call ``provider.complete`` directly and
had NO protection: a hung provider blocked the whole run forever.

``bounded_side_completion`` closes that gap with a **liveness** timeout, not a wall-clock
one (the reference lesson: hermes reverted a wall-clock watchdog because a 30s total
deadline killed slow-but-healthy summary models; the surviving design is per-read idle).
It streams the request and re-aggregates the text, resetting an idle timer on every chunk,
so a model that keeps producing tokens is never killed no matter how long it runs; only a
genuinely silent (stalled) stream trips ``AuxIdleTimeout``. A generous total ceiling bounds
a degenerate trickle. Side calls are text-only by contract, so the re-aggregation is a
plain concatenation — no tool-call/audio handling (that lives in the main loop).

``idle_timeout_seconds=None`` (the default everywhere) is a pure passthrough to
``provider.complete`` — zero behavioural change and zero overhead unless a host opts in.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse

# Floor for the total ceiling relative to the idle timeout: a trickle stream that
# never idles out is still bounded, but the floor stays well above idle so a slow
# healthy stream is never cut (mirrors hermes ``max(600s, 4× task_timeout)``).
_TOTAL_CEILING_IDLE_MULTIPLE = 4
_DEFAULT_TOTAL_CEILING_SECONDS = 600.0


class AuxIdleTimeout(TimeoutError):
    """Raised when a side/aux provider stream stalls (no chunk within the idle window)."""

    def __init__(self, *, idle_timeout_seconds: float, elapsed_chunks: int) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        self.elapsed_chunks = elapsed_chunks
        super().__init__(
            f"Side LLM stream produced no chunk for {idle_timeout_seconds:g}s "
            f"after {elapsed_chunks} chunks"
        )


async def bounded_side_completion(
    provider: Any,
    request: LlmRequest,
    *,
    idle_timeout_seconds: float | None = None,
    total_ceiling_seconds: float | None = None,
) -> LlmResponse:
    """Run a side/aux completion under a liveness (idle) timeout.

    ``idle_timeout_seconds`` None/<=0 → plain ``provider.complete`` (unchanged).
    Otherwise stream, re-aggregate text, reset the idle timer per chunk, and bound
    the total by ``max(total_ceiling_seconds or 600, 4× idle)``. A provider without a
    usable ``stream`` falls back to ``complete`` under the total ceiling only.
    """
    if idle_timeout_seconds is None or idle_timeout_seconds <= 0:
        return await provider.complete(request)

    total_ceiling = max(
        total_ceiling_seconds or _DEFAULT_TOTAL_CEILING_SECONDS,
        _TOTAL_CEILING_IDLE_MULTIPLE * idle_timeout_seconds,
    )
    stream_factory = getattr(provider, "stream", None)
    if not callable(stream_factory):
        return await asyncio.wait_for(
            provider.complete(request), timeout=total_ceiling
        )
    try:
        return await asyncio.wait_for(
            _consume_side_stream(
                provider, request, idle_timeout_seconds=idle_timeout_seconds
            ),
            timeout=total_ceiling,
        )
    except NotImplementedError:
        # Provider declares stream() but doesn't support it — fall back to complete.
        return await asyncio.wait_for(
            provider.complete(request), timeout=total_ceiling
        )


async def _consume_side_stream(
    provider: Any,
    request: LlmRequest,
    *,
    idle_timeout_seconds: float,
) -> LlmResponse:
    """Stream a side request and re-aggregate it into one text LlmResponse."""
    chunks: list[str] = []
    usage = UsageSummary()
    finish_reason = LlmFinishReason.UNKNOWN
    provider_name = getattr(provider, "name", "") or "aux"
    model_name = request.model or "aux-model"
    iterator = provider.stream(request).__aiter__()
    while True:
        try:
            item = await asyncio.wait_for(
                _anext(iterator), timeout=idle_timeout_seconds
            )
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError as exc:
            await _aclose(iterator)
            raise AuxIdleTimeout(
                idle_timeout_seconds=idle_timeout_seconds,
                elapsed_chunks=len(chunks),
            ) from exc
        delta = getattr(item, "delta_text", "") or ""
        if delta:
            chunks.append(delta)
        if getattr(item, "finish_reason", None) is not None:
            finish_reason = item.finish_reason
        item_usage = getattr(item, "usage", None)
        if item_usage is not None:
            usage = item_usage
            model_name = item_usage.model_name or model_name
            provider_name = item_usage.model_provider or provider_name
    return LlmResponse(
        message=ChatMessage(role="assistant", content="".join(chunks)),
        finish_reason=finish_reason,
        usage=usage,
        provider=provider_name,
        model=model_name,
        metadata={"aux_stream_reaggregated": True},
    )


async def _anext(iterator: Any) -> Any:
    return await iterator.__anext__()


async def _aclose(iterator: Any) -> None:
    aclose = getattr(iterator, "aclose", None)
    if callable(aclose):
        try:
            await aclose()
        except BaseException:  # pragma: no cover - cleanup must never mask the timeout
            pass


__all__ = ["AuxIdleTimeout", "bounded_side_completion"]
