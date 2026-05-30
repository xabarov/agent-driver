"""Streaming helpers for single-agent LLM step execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from time import monotonic
from typing import Any, Protocol

import httpx

from agent_driver.contracts import ChatMessage, UsageSummary
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.types import RunContext, RunnerDeps


class StreamingHost(Protocol):
    """Minimal host surface needed for emitting runtime events."""

    _deps: RunnerDeps


class LlmStreamIdleTimeout(httpx.ReadTimeout):
    """Raised when a provider stream stops yielding events mid-run."""

    def __init__(self, *, idle_timeout_seconds: float, emitted_chunks: int) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        self.emitted_chunks = emitted_chunks
        super().__init__(
            "LLM stream produced no events for "
            f"{idle_timeout_seconds:g}s after {emitted_chunks} emitted chunks"
        )


def is_stream_enabled(run_input: AgentRunInput) -> bool:
    """Resolve stream mode from explicit input field or legacy app metadata."""
    if run_input.stream:
        return True
    legacy_flag = run_input.app_metadata.get("stream")
    return bool(legacy_flag)


def emit_token_delta_events(
    host: StreamingHost, context: RunContext, chunks: list[str], *, start_index: int = 0
) -> None:
    """Emit deterministic token delta events in chunk order."""
    for index, chunk in enumerate(chunks, start=start_index):
        if not chunk:
            continue
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.TOKEN_DELTA,
            payload={"index": index, "delta_text": chunk},
        )


def emit_reasoning_delta_events(
    host: StreamingHost, context: RunContext, chunks: list[str], *, start_index: int = 0
) -> None:
    """Emit reasoning-channel delta events (vLLM/Qwen3 chain-of-thought).

    Parallel to ``emit_token_delta_events`` — chunks come from
    ``LlmStreamEvent.delta_reasoning`` (i.e. ``delta.reasoning_content``
    when the provider supports it). Consumers route these into a
    separate reasoning surface (ZION ``ChatReasoning``).
    """
    for index, chunk in enumerate(chunks, start=start_index):
        if not chunk:
            continue
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.REASONING_DELTA,
            payload={"index": index, "delta_reasoning": chunk},
        )


async def complete_streaming_request(
    host: StreamingHost, context: RunContext, request: Any
) -> LlmResponse:
    """Collect streaming provider deltas into one normalized LlmResponse."""
    delta_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    usage = UsageSummary()
    finish_reason = LlmFinishReason.UNKNOWN
    provider_name = host._deps.provider.name
    model_name = request.model or "stream-model"
    stream_metadata: dict[str, Any] = {}
    idle_timeout = _stream_idle_timeout_seconds(context.run_input)
    last_meaningful_event_at = monotonic()
    context.metadata["assistant_stream_started"] = True
    context.metadata["assistant_stream_completed"] = False
    context.metadata["assistant_stream_content"] = ""
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_STARTED,
        payload={"provider": provider_name, "model": model_name},
    )
    iterator = host._deps.provider.stream(request).__aiter__()
    try:
        while True:
            try:
                if idle_timeout is None:
                    item = await anext(iterator)
                else:
                    remaining_timeout = idle_timeout - (
                        monotonic() - last_meaningful_event_at
                    )
                    if remaining_timeout <= 0:
                        raise TimeoutError
                    item = await _anext_with_timeout(
                        iterator,
                        timeout_seconds=remaining_timeout,
                    )
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                raise LlmStreamIdleTimeout(
                    idle_timeout_seconds=idle_timeout or 0.0,
                    emitted_chunks=len(delta_chunks) + len(reasoning_chunks),
                ) from exc

            _collect_stream_item(
                host=host,
                context=context,
                item=item,
                delta_chunks=delta_chunks,
                reasoning_chunks=reasoning_chunks,
                stream_metadata=stream_metadata,
            )
            if _is_meaningful_stream_item(item):
                last_meaningful_event_at = monotonic()
            if item.finish_reason is not None:
                finish_reason = item.finish_reason
            if item.usage is not None:
                usage = item.usage
                model_name = item.usage.model_name or model_name
                provider_name = item.usage.model_provider or provider_name
    finally:
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            with suppress(BaseException):
                await asyncio.wait_for(aclose(), timeout=2.0)
    content = "".join(delta_chunks)
    context.metadata["assistant_stream_completed"] = True
    context.metadata["assistant_stream_content"] = content
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED,
        payload={
            "content": content,
            "finish_reason": finish_reason.value,
            "provider": provider_name,
            "model": model_name,
        },
    )
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=finish_reason,
        usage=usage,
        provider=provider_name,
        model=model_name,
        metadata={
            **request.metadata,
            "token_chunks": delta_chunks,
            "token_chunks_emitted": True,
            **stream_metadata,
        },
    )


def _is_meaningful_stream_item(item: Any) -> bool:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    if item.delta_text or item.delta_reasoning:
        return True
    if item.finish_reason is not None or item.usage is not None:
        return True
    meaningful_metadata_keys = {
        "planned_tool_calls",
        "tool_call_parse_errors",
        "stream_tool_call_delta",
    }
    return any(key in metadata for key in meaningful_metadata_keys)


async def _anext_with_timeout(iterator: Any, *, timeout_seconds: float) -> Any:
    task = asyncio.create_task(anext(iterator))
    done, _pending = await asyncio.wait({task}, timeout=max(0.001, timeout_seconds))
    if task in done:
        return task.result()
    task.cancel()
    task.add_done_callback(_consume_task_exception)
    raise TimeoutError


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    with suppress(BaseException):
        task.result()


def _collect_stream_item(
    *,
    host: StreamingHost,
    context: RunContext,
    item: Any,
    delta_chunks: list[str],
    reasoning_chunks: list[str],
    stream_metadata: dict[str, Any],
) -> None:
    """Append one provider stream event and emit durable deltas."""
    chunk = item.delta_text or ""
    if chunk:
        delta_chunks.append(chunk)
        context.metadata["assistant_stream_content"] = "".join(delta_chunks)
        emit_token_delta_events(
            host, context, [chunk], start_index=len(delta_chunks) - 1
        )
    reasoning_chunk = item.delta_reasoning or ""
    if reasoning_chunk:
        reasoning_chunks.append(reasoning_chunk)
        emit_reasoning_delta_events(
            host,
            context,
            [reasoning_chunk],
            start_index=len(reasoning_chunks) - 1,
        )
    if isinstance(item.metadata, dict):
        if "planned_tool_calls" in item.metadata:
            stream_metadata["planned_tool_calls"] = item.metadata["planned_tool_calls"]
        if "tool_call_parse_errors" in item.metadata:
            stream_metadata["tool_call_parse_errors"] = item.metadata[
                "tool_call_parse_errors"
            ]


def _stream_idle_timeout_seconds(run_input: AgentRunInput) -> float | None:
    raw = run_input.app_metadata.get("llm_stream_idle_timeout_seconds")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    value = float(raw)
    return value if value > 0 else None


__all__ = [
    "LlmStreamIdleTimeout",
    "complete_streaming_request",
    "emit_reasoning_delta_events",
    "emit_token_delta_events",
    "is_stream_enabled",
]
