"""Provider completion and retry loop for the single-agent LLM-call step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.error_classifier import ProviderErrorReason, classify
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    is_forced_tool_choice_provider_error,
    is_invalid_encrypted_reasoning_error,
    is_reduce_max_tokens_credit_error,
    request_with_reduced_max_tokens,
    request_without_forced_tool_choice,
    request_without_tools,
    strip_reasoning_echo,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    emit_non_stream_retry_assistant_message,
    forced_final_no_tools_retry_reason,
    should_retry_empty_forced_final_non_stream,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmStreamIdleTimeout,
    complete_streaming_request,
    is_stream_enabled,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerDeps,
)


class LlmCompletionHost(Protocol):
    """Host surface required while completing a provider request."""

    _deps: RunnerDeps

    def _emit(self, event: EventSpec) -> None: ...


async def complete_request(  # pylint: disable=too-many-branches
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    *,
    recover_context_overflow: Callable[[], Awaitable[Any]] | None = None,
) -> LlmResponse:
    """Complete a provider request with bounded transport/retry handling.

    ``recover_context_overflow`` (optional) is invoked once when the provider
    rejects the request as too long for the context window (the classifier's
    ``CONTEXT_OVERFLOW`` reason). It should compact the run and return a rebuilt,
    smaller request to retry with. A single-shot guard plus the compaction
    circuit breaker prevent retry storms.
    """
    last_timeout: httpx.TimeoutException | None = None
    overflow_recovered = False
    for attempt in range(3):
        try:
            if not is_stream_enabled(context.run_input):
                response = await host._deps.provider.complete(request)
                response = _mark_no_tool_text_form_suppression(
                    context, request, response
                )
                return await retry_forced_final_without_tools(
                    host,
                    context,
                    request=request,
                    response=response,
                )
            response = await complete_streaming_request(host, context, request)
            response = _mark_no_tool_text_form_suppression(context, request, response)
            if should_retry_empty_forced_final_non_stream(context, response):
                context.metadata["empty_forced_final_retry"] = "non_streaming"
                emit_step_event(
                    host,
                    context,
                    event_type=RuntimeEventType.WARNING,
                    payload={
                        "warning": (
                            "Provider returned an empty forced final stream; "
                            "retrying once without streaming."
                        ),
                        "signal_id": "provider_empty_forced_final_non_stream_retry",
                        "severity": "warning",
                    },
                )
                response = await host._deps.provider.complete(
                    request.model_copy(update={"stream": False})
                )
                return await retry_forced_final_without_tools(
                    host,
                    context,
                    request=request,
                    response=response,
                )
            return await retry_forced_final_without_tools(
                host,
                context,
                request=request,
                response=response,
            )
        except httpx.HTTPStatusError as exc:
            if (
                recover_context_overflow is not None
                and not overflow_recovered
                and classify(exc).reason is ProviderErrorReason.CONTEXT_OVERFLOW
            ):
                overflow_recovered = True
                context.metadata["context_overflow_recovery"] = "compacted_and_retried"
                emit_step_event(
                    host,
                    context,
                    event_type=RuntimeEventType.WARNING,
                    payload={
                        "warning": (
                            "Provider rejected the request as too long for the "
                            "context window; compacting and retrying once."
                        ),
                        "signal_id": "provider_context_overflow_compact_retry",
                        "severity": "warning",
                        "status_code": exc.response.status_code,
                    },
                )
                request = await recover_context_overflow()
                continue
            if attempt == 0 and is_invalid_encrypted_reasoning_error(exc):
                stripped = strip_reasoning_echo(request)
                if stripped is not request:
                    context.metadata["reasoning_echo_retry"] = (
                        "stripped_invalid_encrypted_content"
                    )
                    emit_step_event(
                        host,
                        context,
                        event_type=RuntimeEventType.WARNING,
                        payload={
                            "warning": (
                                "Provider rejected echoed encrypted reasoning; "
                                "retrying once without reasoning metadata."
                            ),
                            "signal_id": "provider_invalid_encrypted_reasoning_retry",
                            "severity": "warning",
                        },
                    )
                    request = stripped
                    continue
            if is_forced_tool_choice_provider_error(exc, request):
                context.metadata["forced_tool_choice_retry"] = (
                    "removed_after_provider_rejection"
                )
                emit_step_event(
                    host,
                    context,
                    event_type=RuntimeEventType.WARNING,
                    payload={
                        "warning": (
                            "Provider rejected a forced tool_choice; retrying "
                            "once with the same tools and no forced tool_choice."
                        ),
                        "signal_id": "provider_forced_tool_choice_removed_retry",
                        "severity": "warning",
                        "status_code": exc.response.status_code,
                    },
                )
                request = request_without_forced_tool_choice(request)
                continue
            if is_reduce_max_tokens_credit_error(exc):
                reduced = request_with_reduced_max_tokens(request)
                if reduced is not request:
                    context.metadata["max_tokens_retry"] = "reduced_after_provider_402"
                    emit_step_event(
                        host,
                        context,
                        event_type=RuntimeEventType.WARNING,
                        payload={
                            "warning": (
                                "Provider rejected the requested output budget; "
                                "retrying once with fewer max_tokens."
                            ),
                            "signal_id": "provider_max_tokens_reduced_retry",
                            "severity": "warning",
                            "max_tokens": reduced.max_tokens,
                        },
                    )
                    request = reduced
                    continue
            raise
        except httpx.TimeoutException as exc:
            last_timeout = exc
            if (
                isinstance(exc, LlmStreamIdleTimeout)
                and getattr(exc, "emitted_chunks", 0) > 0
            ):
                raise
            if isinstance(
                exc, LlmStreamIdleTimeout
            ) and _should_retry_stream_failure_without_streaming(
                context, request, attempt
            ):
                return await _retry_stream_failure_without_streaming(
                    host,
                    context,
                    request=request,
                    exc=exc,
                    transition_reason="stream_idle_timeout",
                )
            if attempt == 0:
                continue
            raise
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            if _should_retry_stream_failure_without_streaming(
                context, request, attempt
            ):
                return await _retry_stream_failure_without_streaming(
                    host,
                    context,
                    request=request,
                    exc=exc,
                    transition_reason="provider_stream_open_failed",
                )
            raise
    if last_timeout is not None:
        raise last_timeout
    raise RuntimeError("unreachable")


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


async def _retry_stream_failure_without_streaming(
    host: LlmCompletionHost,
    context: RunContext,
    *,
    request: Any,
    exc: BaseException,
    transition_reason: str,
) -> LlmResponse:
    diagnostics = _stream_failure_retry_diagnostics(
        context,
        request,
        exc,
        provider_name=host._deps.provider.name,
        transition_reason=transition_reason,
    )
    context.metadata["provider_stream_non_stream_fallback"] = True
    context.metadata["provider_stream_fallback_diagnostics"] = diagnostics
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": (
                "Provider stream failed before useful output; "
                "retrying once without streaming."
            ),
            "signal_id": "provider_stream_non_stream_fallback",
            "severity": "warning",
            "provider_diagnostics": diagnostics,
        },
    )
    fallback_response = await host._deps.provider.complete(
        request.model_copy(update={"stream": False})
    )
    fallback_response = _mark_no_tool_text_form_suppression(
        context, request, fallback_response
    )
    emit_non_stream_retry_assistant_message(
        host,
        context,
        fallback_response,
        replacement_reason="provider_stream_non_stream_fallback",
    )
    metadata = dict(fallback_response.metadata or {})
    metadata["provider_stream_non_stream_fallback"] = True
    metadata["provider_stream_fallback_diagnostics"] = diagnostics
    if (fallback_response.message.content or "").strip():
        metadata["token_chunks_emitted"] = True
    fallback_response = fallback_response.model_copy(update={"metadata": metadata})
    return await retry_forced_final_without_tools(
        host,
        context,
        request=request,
        response=fallback_response,
    )


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


async def retry_forced_final_without_tools(
    host: LlmCompletionHost,
    context: RunContext,
    *,
    request: Any,
    response: LlmResponse,
) -> LlmResponse:
    """Retry a forced-final answer with tools disabled when provider leaked tools."""
    retry_reason = forced_final_no_tools_retry_reason(context, request, response)
    if retry_reason is None:
        return response
    signal_id = (
        "provider_forced_final_tool_call_no_tools_retry"
        if retry_reason == "tool_call"
        else "provider_empty_forced_final_no_tools_retry"
    )
    warning = (
        "Provider returned a tool-call shaped forced final answer; retrying once "
        "with tools disabled for a clean final response."
        if retry_reason == "tool_call"
        else (
            "Provider returned an empty forced final answer; retrying once "
            "with tools disabled for a clean final response."
        )
    )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": warning,
            "signal_id": signal_id,
            "severity": "warning",
        },
    )
    context.metadata["forced_final_retry"] = f"{retry_reason}_no_tools"
    if retry_reason == "empty":
        context.metadata["empty_forced_final_retry"] = "no_tools"
    provider_name = str(getattr(host._deps.provider, "name", "") or "")
    retry_response = await host._deps.provider.complete(
        request_without_tools(request, provider_name=provider_name)
    )
    retry_response = _mark_no_tool_text_form_suppression(
        context,
        request,
        retry_response,
        suppress_native_planned=True,
    )
    emit_non_stream_retry_assistant_message(host, context, retry_response)
    return retry_response


def _mark_no_tool_text_form_suppression(
    context: RunContext,
    request: Any,
    response: LlmResponse,
    *,
    suppress_native_planned: bool = False,
) -> LlmResponse:
    """Prevent forced-final/no-tools responses from executing leaked tool calls.

    Some OpenAI-compatible providers can stream tool-call markup as assistant
    text even when the runtime requested ``tool_choice="none"``. The provider
    adapter suppresses parsed text-form tool-call events in that case, but the
    later tool stage also has a compatibility parser over ``message.content``.
    Providers can also return native tool-call metadata despite the no-tools
    request. Preserve that evidence diagnostically while removing executable
    planned calls from the final-only response.
    """
    context_metadata = getattr(context, "metadata", {})
    if not isinstance(context_metadata, dict):
        context_metadata = {}
    if context_metadata.get("force_final_answer") is not True:
        return response
    tool_choice = getattr(request, "tool_choice", None)
    request_tools = getattr(request, "tools", None)
    no_tools_request = not request_tools
    if tool_choice != "none" and not no_tools_request:
        return response
    metadata = dict(response.metadata or {})
    planned = metadata.get("planned_tool_calls")
    if suppress_native_planned and isinstance(planned, list) and planned:
        metadata["suppressed_planned_tool_calls"] = planned
        metadata.pop("planned_tool_calls", None)
    metadata.pop("tool_call_parse_errors", None)
    metadata["suppress_text_form_tool_calls"] = True
    return response.model_copy(update={"metadata": metadata})


__all__ = ["complete_request", "retry_forced_final_without_tools"]
