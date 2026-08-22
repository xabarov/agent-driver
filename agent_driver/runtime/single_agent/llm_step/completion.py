"""Provider completion and retry loop for the single-agent LLM-call step."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, Protocol

import httpx

# F6: monotonic-clock seam so the shared retry-budget deadline is patchable in tests.
_monotonic = time.monotonic

# C3: a per-run redirect probe, isolated per asyncio task (so concurrent subagents each
# have their own steering channel). Set by ``run_subagent(redirect_probe=…)`` around the
# child's run; read by ``_await_with_redirect`` in preference to the shared config probe.
_run_redirect_probe: contextvars.ContextVar[Callable[[], str | None] | None] = (
    contextvars.ContextVar("run_redirect_probe", default=None)
)


@contextlib.contextmanager
def active_redirect_probe(
    probe: Callable[[], str | None] | None,
) -> Iterator[None]:
    """Bind ``probe`` as this run's live redirect probe for the duration of the block.

    Isolated to the current asyncio task's context, so concurrent runs don't share it.
    A ``None`` probe is a no-op (the run falls back to any config-level probe).
    """
    if probe is None:
        yield
        return
    token = _run_redirect_probe.set(probe)
    try:
        yield
    finally:
        _run_redirect_probe.reset(token)


from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.control import CommandQueueItem
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.backoff import abort_aware_sleep, jittered_delay
from agent_driver.llm.context_windows import (
    preferred_history_view,
    provider_model_hint,
)
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.error_classifier import (
    ProviderErrorReason,
    classify,
)
from agent_driver.llm.retry_directives import (
    parse_should_retry,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    affordable_max_tokens_from_error,
    is_forced_tool_choice_provider_error,
    is_invalid_encrypted_reasoning_error,
    is_reduce_max_tokens_credit_error,
    quarantine_inline_reasoning,
    repair_empty_non_final_messages,
    request_with_folded_tool_history,
    request_with_reduced_max_tokens,
    request_without_forced_tool_choice,
    request_without_tools,
    strip_reasoning_echo,
)
from agent_driver.runtime.single_agent.llm_step.provider_routing import (
    resolve_request_provider,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    emit_non_stream_retry_assistant_message,
    forced_final_no_tools_retry_reason,
    should_retry_empty_forced_final_non_stream,
)
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmGenerationSuperseded,
    LlmStreamIdleTimeout,
    complete_streaming_request,
    is_stream_enabled,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerDeps,
)
from agent_driver.runtime.single_agent.llm_step.completion_retry import (  # noqa: F401,E402
    _is_transient_transport_error,
    _is_transient_provider_status,
    _transient_retry_delay,
    _should_retry_stream_failure_without_streaming,
    _stream_has_useful_output,
    _exception_chain,
    _stream_failure_retry_diagnostics,
    _longest_prior_assistant_content,
)


class LlmCompletionHost(Protocol):
    """Host surface required while completing a provider request."""

    _deps: RunnerDeps

    def _emit(self, event: EventSpec) -> None: ...


class RedirectRequested(Exception):
    """Epic 030 B: the host requested a hard redirect during the in-flight LLM
    call. Carries the correction text; the caller re-asks with it as a real user
    turn. Raised only when a ``redirect_probe`` is configured (opt-in)."""

    def __init__(
        self,
        text: str,
        *,
        item: CommandQueueItem | None = None,
        claimant_id: str | None = None,
    ) -> None:
        self.text = text
        self.item = item
        self.claimant_id = claimant_id
        super().__init__("redirect requested")


class AbortRequested(Exception):
    """U4 — the run was aborted while an LLM call was in flight; the call was
    cancelled promptly instead of waiting for the step boundary.

    A dedicated exception (not a ``RuntimeError``/``CancelledError``) so it
    escapes every provider-error ``except`` clause and is mapped explicitly to a
    ``CANCELLED_BY_USER`` terminal by the runner loop — no mis-mapping to
    ``MODEL_ERROR``/``RUNTIME_ERROR``."""


async def _await_with_redirect(
    host: Any,
    coro: Awaitable[Any],
    *,
    abort_check: "Callable[[], bool] | None" = None,
    context: RunContext | None = None,
) -> Any:
    """Await ``coro`` (a provider call) while polling for a redirect or an abort.

    Inert when neither a redirect probe nor ``abort_check`` is configured — just
    awaits. Otherwise races the call: a fired ``abort_check`` cancels the task and
    raises :class:`AbortRequested` (stop wins over redirect); a non-empty probe
    result cancels the task and raises :class:`RedirectRequested`. Only this
    request is cancelled — never tools/children.
    """
    # C3: prefer this run's per-run probe (set by run_subagent for live steering) over
    # the shared config-level probe.
    probe = _run_redirect_probe.get() or getattr(
        getattr(host, "_config", None), "redirect_probe", None
    )
    durable_store = (
        getattr(getattr(host, "_deps", None), "command_queue_store", None)
        if context is not None
        else None
    )
    durable_claim = getattr(durable_store, "claim_hard_redirect", None)
    if probe is None and abort_check is None and not callable(durable_claim):
        return await coro
    task = asyncio.ensure_future(coro)
    await_generation = (
        int(context.metadata.get("llm_generation") or 0) if context is not None else 0
    )
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.1)
        if task in done:
            return task.result()
        if abort_check is not None:
            try:
                aborted = bool(abort_check())
            except Exception:  # noqa: BLE001 - a bad check must not kill the call
                aborted = False
            if aborted:
                _cancel_detached(task)
                raise AbortRequested("run aborted during LLM call")
        if context is not None:
            get_run_state = getattr(durable_store, "get_run_state", None)
            if callable(get_run_state):
                try:
                    live_state = get_run_state(context.run_id)
                except Exception:
                    live_state = None
                if live_state is not None and live_state.stopped:
                    _cancel_detached(task)
                    raise AbortRequested("durable Stop accepted during LLM call")
            if callable(durable_claim):
                item = durable_claim(
                    run_id=context.run_id,
                    claimant_id=context.attempt_id,
                    expected_generation=await_generation,
                )
                if item is not None:
                    text = item.payload.get("message") or item.payload.get("text")
                    if isinstance(text, str) and text.strip():
                        context.metadata["llm_generation"] = item.llm_generation
                        _cancel_detached(task)
                        raise RedirectRequested(
                            text.strip(), item=item, claimant_id=context.attempt_id
                        )
            current_generation = getattr(durable_store, "current_llm_generation", None)
            if callable(current_generation):
                try:
                    durable_generation = int(current_generation(context.run_id))
                except Exception as exc:
                    _cancel_detached(task)
                    raise LlmGenerationSuperseded(
                        "LLM generation fence became unavailable"
                    ) from exc
                if durable_generation != await_generation:
                    _cancel_detached(task)
                    raise LlmGenerationSuperseded(
                        f"LLM generation {await_generation} was superseded by "
                        f"{durable_generation}"
                    )
        if probe is not None:
            try:
                text = probe()
            except Exception:  # noqa: BLE001 - a bad probe must not kill the call
                text = None
            if text:
                _cancel_detached(task)
                raise RedirectRequested(str(text))


def _cancel_detached(task: "asyncio.Task[Any]") -> None:
    """Request local cancellation without waiting on an uncooperative provider."""
    task.cancel()

    def _consume(done: "asyncio.Task[Any]") -> None:
        try:
            done.result()
        except BaseException:  # cancellation/provider late failure is quarantined
            pass

    task.add_done_callback(_consume)


def _emit_provider_retry_warning(
    host: LlmCompletionHost,
    context: RunContext,
    *,
    warning: str,
    signal_id: str,
    **extra: object,
) -> None:
    """Emit the standard provider-retry WARNING step event (``severity`` warning)
    carrying ``warning`` / ``signal_id`` plus any extra diagnostic fields."""
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": warning,
            "signal_id": signal_id,
            "severity": "warning",
            **extra,
        },
    )


async def _attempt_completion(
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    *,
    abort_check: Callable[[], bool] | None,
) -> LlmResponse:
    """Run a single provider completion attempt (streaming or not) with the redirect
    observer + no-tool text-form suppression marking, plus the empty-forced-final
    one-shot non-streaming retry, and return the forced-final-normalised response."""
    if not is_stream_enabled(context.run_input):
        response = await _await_with_redirect(
            host,
            # R3: route by the run's model_role (default → primary provider).
            resolve_request_provider(host, request).complete(request),
            abort_check=abort_check,
            context=context,
        )
        response = _mark_no_tool_text_form_suppression(context, request, response)
        return await retry_forced_final_without_tools(
            host,
            context,
            request=request,
            response=response,
        )
    response = await _await_with_redirect(
        host,
        complete_streaming_request(host, context, request),
        abort_check=abort_check,
        context=context,
    )
    response = _mark_no_tool_text_form_suppression(context, request, response)
    if should_retry_empty_forced_final_non_stream(context, response):
        context.metadata["empty_forced_final_retry"] = "non_streaming"
        _emit_provider_retry_warning(
            host,
            context,
            warning=(
                "Provider returned an empty forced final stream; "
                "retrying once without streaming."
            ),
            signal_id="provider_empty_forced_final_non_stream_retry",
        )
        response = await resolve_request_provider(host, request).complete(
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


async def _handle_completion_status_error(
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    exc: httpx.HTTPStatusError,
    *,
    attempt: int,
    recover_context_overflow: Callable[[], Awaitable[Any]] | None,
    overflow_recovered: bool,
) -> tuple[Any, bool]:
    """Apply the provider-status-error retry rules inside the completion loop, in
    order: one-shot context-overflow compaction, stripped encrypted-reasoning echo
    (attempt 0), removed forced tool_choice, reduced max_tokens (402 credit), and a
    bounded transient-status backoff. Returns the ``(request, overflow_recovered)`` to
    retry with; re-raises ``exc`` when no rule applies."""
    if (
        recover_context_overflow is not None
        and not overflow_recovered
        and classify(exc).reason is ProviderErrorReason.CONTEXT_OVERFLOW
    ):
        overflow_recovered = True
        context.metadata["context_overflow_recovery"] = "compacted_and_retried"
        _emit_provider_retry_warning(
            host,
            context,
            warning=(
                "Provider rejected the request as too long for the "
                "context window; compacting and retrying once."
            ),
            signal_id="provider_context_overflow_compact_retry",
            status_code=exc.response.status_code,
        )
        request = await recover_context_overflow()
        return request, overflow_recovered
    if attempt == 0 and is_invalid_encrypted_reasoning_error(exc):
        stripped = strip_reasoning_echo(request)
        if stripped is not request:
            context.metadata["reasoning_echo_retry"] = (
                "stripped_invalid_encrypted_content"
            )
            _emit_provider_retry_warning(
                host,
                context,
                warning=(
                    "Provider rejected echoed encrypted reasoning; "
                    "retrying once without reasoning metadata."
                ),
                signal_id="provider_invalid_encrypted_reasoning_retry",
            )
            return stripped, overflow_recovered
    if is_forced_tool_choice_provider_error(exc, request):
        context.metadata["forced_tool_choice_retry"] = (
            "removed_after_provider_rejection"
        )
        _emit_provider_retry_warning(
            host,
            context,
            warning=(
                "Provider rejected a forced tool_choice; retrying "
                "once with the same tools and no forced tool_choice."
            ),
            signal_id="provider_forced_tool_choice_removed_retry",
            status_code=exc.response.status_code,
        )
        return request_without_forced_tool_choice(request), overflow_recovered
    if is_reduce_max_tokens_credit_error(exc):
        affordable = affordable_max_tokens_from_error(exc)
        reduced = request_with_reduced_max_tokens(request, affordable)
        if reduced is not request:
            context.metadata["max_tokens_retry"] = "reduced_after_provider_402"
            _emit_provider_retry_warning(
                host,
                context,
                warning=(
                    "Provider rejected the requested output budget; "
                    "retrying once with fewer max_tokens."
                ),
                signal_id="provider_max_tokens_reduced_retry",
                max_tokens=reduced.max_tokens,
            )
            return reduced, overflow_recovered
    # F3: a server ``x-should-retry: false`` means retrying won't clear the error —
    # skip the transient retry and let it raise instead of burning an attempt.
    if (
        attempt < 2
        and _is_transient_provider_status(exc)
        and parse_should_retry(exc.response.headers) is not False
    ):
        delay = _transient_retry_delay(exc, attempt)
        context.metadata["transient_provider_retries"] = attempt + 1
        _emit_provider_retry_warning(
            host,
            context,
            warning=(
                "Provider returned a transient error "
                f"(HTTP {exc.response.status_code}); retrying "
                f"in {delay:g}s."
            ),
            signal_id="provider_transient_error_retry",
            status_code=exc.response.status_code,
            retry_attempt=attempt + 1,
            retry_in_seconds=delay,
        )
        await abort_aware_sleep(
            jittered_delay(delay), abort_check=_context_abort_check(context)
        )
        return request, overflow_recovered
    raise exc


def _context_abort_check(context: RunContext) -> Callable[[], bool] | None:
    """A cooperative-abort probe for the run, or ``None`` when no handle is set."""
    handle = getattr(context, "abort_handle", None)
    if handle is None:
        return None
    return lambda: bool(getattr(handle, "is_aborted", False))


async def complete_request(
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    *,
    recover_context_overflow: Callable[[], Awaitable[Any]] | None = None,
) -> LlmResponse:
    """Complete a provider request, with an optional ordered model-fallback (F4).

    Runs :func:`_complete_request_attempts` (bounded per-error retry on the primary
    model) and, when a run configures ``fallback_models``, retries the *whole*
    attempt on each fallback model in turn if the primary fails with a non-fatal
    error — a model unavailable / rate-limited / overloaded gives way to the next
    one, gated by the same ``is_fatal`` rule the provider-fallback uses (auth,
    content-policy and context-overflow never fall back to another model). Cost and
    events accumulate on the shared host, so fallback spend rolls into the run.
    """
    fallback_models = tuple(
        getattr(getattr(host, "_deps", None), "fallback_models", ()) or ()
    )
    if not fallback_models:
        return await _complete_request_attempts(
            host, context, request, recover_context_overflow=recover_context_overflow
        )

    models = [getattr(request, "model", None), *fallback_models]
    last_exc: BaseException | None = None
    for index, model in enumerate(models):
        attempt_request = (
            request if index == 0 else request.model_copy(update={"model": model})
        )
        try:
            return await _complete_request_attempts(
                host,
                context,
                attempt_request,
                recover_context_overflow=recover_context_overflow,
            )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            last_exc = exc
            classified = classify(exc)
            # Fatal-to-rotation (auth / content-policy / context-overflow) or the
            # last model in the chain → give up; a different model won't help.
            if classified.is_fatal or index >= len(models) - 1:
                raise
            next_model = models[index + 1]
            context.metadata["model_fallbacks"] = index + 1
            _emit_provider_retry_warning(
                host,
                context,
                warning=(
                    f"Model '{model}' failed ({classified.reason.value}); "
                    f"falling back to '{next_model}'."
                ),
                signal_id="model_fallback",
                failed_model=str(model),
                next_model=str(next_model),
                fallback_index=index + 1,
            )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable")


async def _complete_request_attempts(
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
    last_exc: BaseException | None = None
    overflow_recovered = False
    # U4 — observe the run's abort while the provider call is in flight so a stop
    # cancels it promptly (instead of waiting for the next step boundary).
    abort_check = _context_abort_check(context)
    # F6: a single shared wall-clock retry budget bounds this loop end-to-end.
    # base.py (per provider call) and this loop each retry transient errors, so on a
    # persistently-failing provider the two multiply (base ~4 × this ~3). Once the
    # cumulative time here passes the budget we stop re-entering the provider instead
    # of compounding. ``None`` (default) preserves the plain 3-attempt behavior.
    retry_budget = getattr(
        getattr(host, "_deps", None), "completion_retry_budget_seconds", None
    )
    started = _monotonic()
    for attempt in range(3):
        if (
            attempt > 0
            and retry_budget is not None
            and _monotonic() - started >= retry_budget
        ):
            # Shared retry budget exhausted — surface the last error rather than
            # starting another (base-multiplying) attempt.
            if last_exc is not None:
                raise last_exc
            break
        # Single pre-send owner (epic 043 B): pad empty non-final turns so a
        # degenerate/interrupted turn can't make a strict provider reject the
        # whole request. Covers the initial send and every retry rebuild below
        # (each reassigns ``request`` then ``continue``s back through here).
        request = repair_empty_non_final_messages(request)
        try:
            return await _attempt_completion(
                host, context, request, abort_check=abort_check
            )
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            request, overflow_recovered = await _handle_completion_status_error(
                host,
                context,
                request,
                exc,
                attempt=attempt,
                recover_context_overflow=recover_context_overflow,
                overflow_recovered=overflow_recovered,
            )
            continue
        except httpx.TimeoutException as exc:
            last_timeout = exc
            last_exc = exc
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
            last_exc = exc
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
            # Transient connection/transport hiccups — ConnectError ("All
            # connection attempts failed"), RemoteProtocolError ("Server
            # disconnected"), the sibling-teardown ReadError, and the wrapping
            # ``ProviderTransportError`` — usually clear within seconds (unlike a
            # deterministic 4xx). The status/timeout branches above already retry
            # their classes; this closes the gap where a network blip fails the
            # whole run instead of a bounded blind retry.
            if attempt < 2 and _is_transient_transport_error(exc):
                delay = 2.0 * (attempt + 1)
                context.metadata["transient_transport_retries"] = attempt + 1
                _emit_provider_retry_warning(
                    host,
                    context,
                    warning=(
                        "Provider transport error; retrying after a short " "backoff."
                    ),
                    signal_id="provider_transient_transport_retry",
                    error=str(exc)[:200],
                    attempt=attempt + 1,
                )
                await abort_aware_sleep(jittered_delay(delay), abort_check=abort_check)
                continue
            raise
    if last_timeout is not None:
        raise last_timeout
    raise RuntimeError("unreachable")


async def _retry_stream_failure_without_streaming(
    host: LlmCompletionHost,
    context: RunContext,
    *,
    request: Any,
    exc: BaseException,
    transition_reason: str,
) -> LlmResponse:
    provider = resolve_request_provider(host, request)  # R3: route by model_role
    diagnostics = _stream_failure_retry_diagnostics(
        context,
        request,
        exc,
        provider_name=provider.name,
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
    fallback_response = await provider.complete(
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


async def _recover_forced_final_via_fallback_providers(
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    retry_response: LlmResponse,
    *,
    folded_request: Any,
    unusable: Callable[[str | None], bool],
) -> LlmResponse:
    """Ladder step: while the retry is still unusable, try each configured fallback
    provider once with the same (folded) request — the empty-final quirk is often
    model/provider-specific, so a sibling provider given the SAME request frequently
    answers. Reference: hermes ``_fallback_chain``. No-op when already usable."""
    if not unusable(retry_response.message.content):
        return retry_response
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
        if not unusable(fallback_response.message.content):
            context.metadata["forced_final_fallback_provider"] = fallback_name
            return fallback_response
    return retry_response


def _recover_forced_final_via_prior_turn(
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    retry_response: LlmResponse,
    *,
    provider_name: str,
    unusable: Callable[[str | None], bool],
) -> LlmResponse:
    """Ladder step: when the retry is still unusable but THIS run already produced a
    substantive assistant text earlier in the loop, finalize with that rather than
    with nothing (provenance-flagged). Reference: hermes ``fallback_prior_turn_content``.
    """
    if not unusable(retry_response.message.content):
        return retry_response
    prior = _longest_prior_assistant_content(request)
    if not prior:
        return retry_response
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
    return LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=prior),
        finish_reason=LlmFinishReason.UNKNOWN,
        provider=provider_name,
        model=str(getattr(request, "model", "") or ""),
        metadata={"forced_final_prior_turn_recovered": True},
    )


async def _recover_forced_final_via_quarantine(
    host: LlmCompletionHost,
    context: RunContext,
    request: Any,
    retry_response: LlmResponse,
    *,
    provider_name: str,
    unusable: Callable[[str | None], bool],
) -> LlmResponse:
    """Ladder step (epic 043 D): a persistent empty streak whose history still carries
    an assistant turn exposing its own chain-of-thought is the transcript-poisoning
    signature — a provider classifier reads the replayed CoT as a prefill injection and
    blanks every call. Sanitize the suspect turn(s) and retry ONCE (bounded, mirrors
    strip_reasoning_echo). No-op when already usable or already attempted."""
    if not unusable(retry_response.message.content) or context.metadata.get(
        "poisoned_prefix_quarantine_attempted"
    ):
        return retry_response
    quarantined, suspect_count = quarantine_inline_reasoning(request)
    if not suspect_count:
        return retry_response
    context.metadata["poisoned_prefix_quarantine_attempted"] = True
    context.metadata["poisoned_prefix_suspect_turns"] = suspect_count
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": (
                "Empty-final ladder exhausted with inline reasoning present "
                f"in {suspect_count} assistant turn(s) — suspected poisoned "
                "prefix; sanitizing and retrying once."
            ),
            "signal_id": "poisoned_prefix_suspect",
            "severity": "warning",
            "suspect_turns": suspect_count,
        },
    )
    quarantine_response = _mark_no_tool_text_form_suppression(
        context,
        request,
        await resolve_request_provider(host, request).complete(
            request_without_tools(quarantined, provider_name=provider_name)
        ),
        suppress_native_planned=True,
    )
    if not unusable(quarantine_response.message.content):
        context.metadata["poisoned_prefix_quarantine_recovered"] = True
        return quarantine_response
    return retry_response


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
    provider = resolve_request_provider(host, request)  # R3: route by model_role
    provider_name = str(getattr(provider, "name", "") or "")
    model_hint = provider_model_hint(provider) or str(
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
        return await provider.complete(
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
        return await provider.complete(folded_request)

    def _unusable(content: str | None) -> bool:
        """Ladder-step failure predicate — the shared engine-wide definition."""
        from agent_driver.runtime.single_agent.finalization.answer_recovery import (  # pylint: disable=import-outside-toplevel
            final_content_unusable,
        )

        return final_content_unusable(
            content, str(getattr(context.run_input, "input", "") or "")
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
    retry_response = await _recover_forced_final_via_fallback_providers(
        host,
        context,
        request,
        retry_response,
        folded_request=folded_request,
        unusable=_unusable,
    )
    retry_response = _recover_forced_final_via_prior_turn(
        host,
        context,
        request,
        retry_response,
        provider_name=provider_name,
        unusable=_unusable,
    )
    retry_response = await _recover_forced_final_via_quarantine(
        host,
        context,
        request,
        retry_response,
        provider_name=provider_name,
        unusable=_unusable,
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
                    "fallback-provider, prior-turn, poisoned-prefix quarantine)."
                ),
                "signal_id": "forced_final_empty_after_all_retries",
                "severity": "error",
            },
        )
        context.metadata["forced_final_empty_after_all_retries"] = True
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
