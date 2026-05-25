"""Streaming helpers for single-agent LLM step execution."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.contracts import ChatMessage, UsageSummary
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.types import RunContext, RunnerDeps


class StreamingHost(Protocol):
    """Minimal host surface needed for emitting runtime events."""

    _deps: RunnerDeps


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
    async for item in host._deps.provider.stream(request):
        chunk = item.delta_text or ""
        if chunk:
            delta_chunks.append(chunk)
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
        if item.finish_reason is not None:
            finish_reason = item.finish_reason
        if item.usage is not None:
            usage = item.usage
            model_name = item.usage.model_name or model_name
            provider_name = item.usage.model_provider or provider_name
    return LlmResponse(
        message=ChatMessage(role="assistant", content="".join(delta_chunks)),
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


__all__ = [
    "complete_streaming_request",
    "emit_reasoning_delta_events",
    "emit_token_delta_events",
    "is_stream_enabled",
]
