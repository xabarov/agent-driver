"""Provider completion and retry loop for the single-agent LLM-call step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.context_windows import (
    preferred_history_view,
    provider_model_hint,
)
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.error_classifier import ProviderErrorReason, classify
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    is_forced_tool_choice_provider_error,
    is_invalid_encrypted_reasoning_error,
    is_reduce_max_tokens_credit_error,
    request_with_reduced_max_tokens,
    request_without_forced_tool_choice,
    request_with_folded_tool_history,
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
    """Recovery ladder for tool-shaped/empty forced finals (epics 016 + 018).

    Strategy ORDER is model-profile aware (epic 018 phase C): models whose forced
    finals are unreliable on the tool-protocol history shape (deepseek family) get
    the FOLDED plain view first — the native no-tools retry predictably comes back
    empty there and only wastes a provider call. Everyone else keeps native-first.
    Ladder: profile-first shape → other shape → fallback providers → prior-turn →
    honest terminal signal.
    """
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
    provider_name = str(getattr(host._deps.provider, "name", "") or "")
    model_hint = provider_model_hint(host._deps.provider) or str(
        getattr(request, "model", "") or ""
    )
    folded_request = request_with_folded_tool_history(
        request, provider_name=provider_name
    )
    # Phase C revision (live counter-evidence 2026-07-19): folded-FIRST regressed deepseek —
    # the folded view triggers its canned wrong-language refusal on ~2/3 of forced finals,
    # while the native no-tools retry succeeds when the history is well-formed. Order stays
    # native-first for every profile; the profile keeps the fold step ARMED (and the ladder
    # now treats canned refusals as empty, so the fold still fires when native degenerates).
    _ = preferred_history_view(model_hint)  # profile retained for observability/hosts
    prefer_folded = False

    async def _try_no_tools() -> LlmResponse:
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
        return await host._deps.provider.complete(
            request_without_tools(request, provider_name=provider_name)
        )

    async def _try_fold() -> LlmResponse | None:
        if folded_request is request:
            return None
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.WARNING,
            payload={
                "warning": (
                    "Retrying the forced final with the tool history folded into "
                    "plain messages"
                    + (
                        " (model profile prefers the folded view)."
                        if prefer_folded
                        else " after an empty native retry."
                    )
                ),
                "signal_id": "provider_empty_forced_final_history_fold_retry",
                "severity": "warning",
            },
        )
        context.metadata["empty_forced_final_retry"] = "history_fold"
        return await host._deps.provider.complete(folded_request)

    def _unusable(content: str | None) -> bool:
        """Empty OR degenerate canned/wrong-language refusal (epic 015 detector): both mean
        the strategy failed and the ladder should continue."""
        from agent_driver.runtime.single_agent.finalization.answer_recovery import (  # pylint: disable=import-outside-toplevel
            is_degenerate_refusal,
        )

        text = (content or "").strip()
        if not text:
            return True
        return is_degenerate_refusal(
            text, str(getattr(context.run_input, "input", "") or "")
        )

    first, second = (
        (_try_fold, _try_no_tools) if prefer_folded else (_try_no_tools, _try_fold)
    )
    retry_response = await first()
    if retry_response is None:
        retry_response = await second()
        second = None  # type: ignore[assignment]
    retry_response = _mark_no_tool_text_form_suppression(
        context,
        request,
        retry_response,
        suppress_native_planned=True,
    )
    if _unusable(retry_response.message.content) and second is not None:
        alternate = await second()
        if alternate is not None:
            retry_response = _mark_no_tool_text_form_suppression(
                context,
                request,
                alternate,
                suppress_native_planned=True,
            )
    if _unusable(retry_response.message.content):
        # Fallback-provider step (reference: hermes _fallback_chain): the empty-final
        # quirk is model/provider-specific — a sibling provider given the SAME folded
        # request often answers normally. Tried before prior-turn recovery because a
        # fresh full answer beats an earlier partial one.
        for fallback in getattr(host._deps, "fallback_providers", ()) or ():
            fallback_name = str(getattr(fallback, "name", "") or "fallback")
            emit_step_event(
                host,
                context,
                event_type=RuntimeEventType.WARNING,
                payload={
                    "warning": (
                        "Forced-final retries on the primary provider returned empty; "
                        f"retrying once via fallback provider '{fallback_name}'."
                    ),
                    "signal_id": "provider_empty_forced_final_fallback_provider_retry",
                    "severity": "warning",
                    "fallback_provider": fallback_name,
                },
            )
            context.metadata["empty_forced_final_retry"] = "fallback_provider"
            try:
                fallback_response = await fallback.complete(
                    folded_request if folded_request is not request else request
                )
            except Exception:  # pylint: disable=broad-except
                # Ladder step must not turn a recoverable empty into a hard failure.
                continue
            if not _unusable(fallback_response.message.content):
                context.metadata["forced_final_fallback_provider"] = fallback_name
                retry_response = fallback_response
                break
    if _unusable(retry_response.message.content):
        # Prior-turn substantive fallback (reference: hermes fallback_prior_turn_content):
        # if THIS run already produced a substantive assistant text earlier in the loop,
        # finalize with it rather than with nothing. Provenance-flagged so hosts can tell.
        prior = _longest_prior_assistant_content(request)
        if prior:
            emit_step_event(
                host,
                context,
                event_type=RuntimeEventType.WARNING,
                payload={
                    "warning": (
                        "All forced-final retries returned empty; finalizing with the "
                        "run's own earlier substantive assistant message."
                    ),
                    "signal_id": "forced_final_recovered_prior_turn",
                    "severity": "warning",
                    "chars": len(prior),
                },
            )
            context.metadata["forced_final_prior_turn_recovered"] = True
            retry_response = LlmResponse(
                message=ChatMessage(role=ChatRole.ASSISTANT, content=prior),
                finish_reason=LlmFinishReason.UNKNOWN,
                provider=provider_name,
                model=str(getattr(request, "model", "") or ""),
                metadata={"forced_final_prior_turn_recovered": True},
            )
    if _unusable(retry_response.message.content):
        # All recovery strategies exhausted: surface a distinct signal so hosts can
        # message the user honestly instead of rendering a silent empty bubble.
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.WARNING,
            payload={
                "warning": (
                    "Provider returned an empty final answer after all retry "
                    "strategies (non-stream, no-tools, history-fold, "
                    "fallback-provider, prior-turn)."
                ),
                "signal_id": "forced_final_empty_after_all_retries",
                "severity": "error",
            },
        )
        context.metadata["forced_final_empty_after_all_retries"] = True
    emit_non_stream_retry_assistant_message(host, context, retry_response)
    return retry_response


# Floor for adopting a prior assistant message as the forced-final answer. Mirrors the
# partial-final stream recovery floor (200 chars): shorter texts are usually loop
# narration («ищу дальше…»), not an answer worth finalizing with.
_PRIOR_TURN_MIN_CHARS = 200


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
