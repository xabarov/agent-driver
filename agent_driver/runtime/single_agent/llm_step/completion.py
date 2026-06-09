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
                return await retry_forced_final_without_tools(
                    host,
                    context,
                    request=request,
                    response=response,
                )
            response = await complete_streaming_request(host, context, request)
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
            return response
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
            if attempt == 0:
                continue
            raise
    if last_timeout is not None:
        raise last_timeout
    raise RuntimeError("unreachable")


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
    emit_non_stream_retry_assistant_message(host, context, retry_response)
    return retry_response


__all__ = ["complete_request", "retry_forced_final_without_tools"]
