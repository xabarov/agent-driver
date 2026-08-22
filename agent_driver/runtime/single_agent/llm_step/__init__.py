"""LLM call step for single-agent runtime."""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from agent_driver.contracts.control import LiveMessagePhase
from agent_driver.contracts.enums import (
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.llm.base import provider_request_id as _provider_request_id
from agent_driver.llm.payload_debug import (
    debug_llm_payload_enabled,
    summarize_llm_request_payload,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.control.live_messages import (
    live_message_receipt,
    live_message_transition_event,
)
from agent_driver.runtime.control.steering_framing import redirect_correction_frame
from agent_driver.runtime.lifecycle_hooks import (
    dispatch_after_llm,
    dispatch_before_llm,
)
from agent_driver.context.token_estimation import (
    DEFAULT_CHARS_PER_TOKEN,
    calibrate_chars_per_token,
)
from agent_driver.runtime.metadata_state import (
    StreamingRuntimeState,
    get_compaction_runtime_state,
    get_cost_runtime_state,
    get_loop_control_state,
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    CompactionStageHost,
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.llm_step.completion import (
    RedirectRequested as _RedirectRequested,
)
from agent_driver.runtime.single_agent.llm_step.completion import (
    complete_request as _complete_request,
)
from agent_driver.runtime.single_agent.llm_step.completion import (
    retry_forced_final_without_tools as _retry_forced_final_without_tools,
)
from agent_driver.runtime.single_agent.llm_step.context_pressure import (
    emit_token_pressure_warning as _emit_token_pressure_warning,
)
from agent_driver.runtime.single_agent.llm_step.context_pressure import (
    request_with_context_pressure_nudge as _request_with_context_pressure_nudge,
)
from agent_driver.runtime.single_agent.llm_step.prompt import (
    effective_code_agent_imports as _effective_code_agent_imports,
)
from agent_driver.runtime.single_agent.llm_step.prompt import (
    react_system_instruction as _react_system_instruction,
)
from agent_driver.runtime.single_agent.llm_step.prompt import (
    runtime_attachment_messages,
)
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    narrow_request_tools_to_forced_choice as _narrow_request_tools_to_forced_choice,
)
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    provider_error_message as _provider_error_message,
)
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    request_tool_name as _request_tool_name,
)
from agent_driver.runtime.single_agent.llm_step.request import (
    build_trimmed_request as _build_trimmed_request,
)
from agent_driver.runtime.single_agent.llm_step.request import (
    emit_protocol_debug as _emit_protocol_debug,
)
from agent_driver.runtime.single_agent.llm_step.request import (
    microcompact_context_observations as _microcompact_context_observations,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    emit_partial_assistant_tombstone as _emit_partial_assistant_tombstone,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    force_final_answer_message as _force_final_answer_message,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    recover_force_final_stream_response as _recover_force_final_stream_response,
)
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmStreamIdleTimeout,
    emit_token_delta_events,
)
from agent_driver.runtime.single_agent.planning.state import build_planning_snapshot
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
    RuntimeStepResult,
)


class LlmStepHost(CompactionStageHost, Protocol):
    """Host surface for LLM step execution."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: EventSpec) -> None: ...
    def _save_checkpoint(
        self, context: RunContext, *, latest_output: Any, node_id: str
    ) -> Any: ...
    def _maybe_fail_after_step(self, step_name: str) -> None: ...


_runtime_attachment_messages = runtime_attachment_messages

# Cap per-message content on LLM spans so a long prompt doesn't bloat the trace.
_SPAN_MSG_MAX_CHARS = 4000
_DIAGNOSTIC_HEADER_ALLOWLIST = (
    "x-request-id",
    "request-id",
    "x-correlation-id",
    "cf-ray",
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "content-type",
)


def _content_for_span(message: Any) -> str:
    """Stringify a ChatMessage's content for an OpenInference message attribute."""
    content = getattr(message, "content", message)
    text = content if isinstance(content, str) else str(content)
    if len(text) > _SPAN_MSG_MAX_CHARS:
        return text[:_SPAN_MSG_MAX_CHARS] + "…"
    return text


def _messages_for_span(messages: Any) -> list[dict[str, str]]:
    """Build {role, content} dicts for llm.input_messages.* span attributes."""
    out: list[dict[str, str]] = []
    for msg in messages or []:
        out.append(
            {
                "role": str(getattr(msg, "role", "")),
                "content": _content_for_span(msg),
            }
        )
    return out


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


def _provider_failure_diagnostics(
    host: LlmStepHost,
    context: RunContext,
    request: Any,
    exc: BaseException,
    *,
    transition_reason: str,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "transition_reason": transition_reason,
        "provider": host._deps.provider.name,
        "model": getattr(request, "model", None) or "stream-model",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "assistant_stream_started": context.metadata.get("assistant_stream_started")
        is True,
        "assistant_stream_completed": context.metadata.get("assistant_stream_completed")
        is True,
        "assistant_stream_tombstoned": context.metadata.get(
            "assistant_stream_tombstoned"
        )
        is True,
        "assistant_stream_tool_intent_seen": context.metadata.get(
            "assistant_stream_tool_intent_seen"
        )
        is True,
        "assistant_stream_content_chars": len(
            context.metadata.get("assistant_stream_content") or ""
        ),
        "stream_events_seen": int(
            context.metadata.get("assistant_stream_events_seen") or 0
        ),
        "token_chunks_seen": int(
            context.metadata.get("assistant_stream_token_chunks_seen") or 0
        ),
        "reasoning_chunks_seen": int(
            context.metadata.get("assistant_stream_reasoning_chunks_seen") or 0
        ),
    }
    started_at = context.metadata.get("llm_call_started_monotonic")
    if isinstance(started_at, (int, float)):
        diagnostics["duration_ms"] = round(
            max(0.0, (time.monotonic() - float(started_at)) * 1000.0),
            2,
        )
    if isinstance(exc, LlmStreamIdleTimeout):
        diagnostics["idle_timeout_seconds"] = exc.idle_timeout_seconds
        diagnostics["idle_timeout_emitted_chunks"] = exc.emitted_chunks
    if isinstance(exc, httpx.HTTPStatusError):
        diagnostics["status_code"] = exc.response.status_code
        request_id = _provider_request_id(exc.response.headers)
        if request_id:
            diagnostics["provider_request_id"] = request_id
        headers = {
            key.lower(): value
            for key, value in exc.response.headers.items()
            if key.lower() in _DIAGNOSTIC_HEADER_ALLOWLIST
        }
        if headers:
            diagnostics["response_headers"] = headers
    chain = _exception_chain(exc)
    if chain:
        diagnostics["exception_chain"] = chain
    return diagnostics


def _overflow_recovery(
    host: LlmStepHost, context: RunContext, request: Any, clarification: Any
):
    """Build the CONTEXT_OVERFLOW recovery callback for ``complete_request``.

    On overflow the provider says the prompt is too long for its context
    window, so force a compaction (treat the run as max token pressure — it is
    genuinely over budget) and rebuild a smaller request from the now-compacted
    context. The compaction circuit breaker bounds repeated attempts.
    """

    async def _recover() -> Any:
        await apply_compaction_if_eligible(
            host, context=context, request=request, token_pressure_state="blocking"
        )
        observations = _microcompact_context_observations(host, context)
        rebuilt, _ = _build_trimmed_request(host, context, observations, clarification)
        # EPIC-10: last-resort emergency strip on the REBUILT request. The prompt is
        # genuinely over the provider's hard window, and when LLM compaction is disabled
        # the graduated pre-passes may not free enough — so aggressively clear old tool
        # results and hard-cap any oversized message (embedded blob / media) so the
        # single overflow retry is materially smaller. Runs on the ephemeral request
        # only; the durable log is untouched.
        if getattr(host._config, "overflow_emergency_strip_enabled", False):
            from agent_driver.runtime.single_agent.context_management.context_window_recovery import (  # noqa: PLC0415,E501
                emergency_strip_oversized_payloads,
            )

            stripped_messages, strip_audit = emergency_strip_oversized_payloads(
                list(getattr(rebuilt, "messages", []) or []),
                max_message_chars=int(
                    getattr(host._config, "overflow_strip_max_message_chars", 20_000)
                    or 20_000
                ),
            )
            if strip_audit.get("cleared") or strip_audit.get("truncated"):
                rebuilt.messages = stripped_messages
                context.metadata["context_overflow_emergency_strip"] = strip_audit
        return _narrow_request_tools_to_forced_choice(rebuilt)

    return _recover


def _preserved_partial_output(context: RunContext) -> str:
    """Return the partial assistant text streamed before a mid-flight abort, if any.

    On a hard-redirect abort the streaming task is cancelled (``_cancel_detached``),
    but each chunk had been mirrored into ``assistant_stream_content`` and the stream
    was never ``mark_completed``. That surviving buffer is the model's partial answer
    (text only — signed reasoning is streamed separately and never replayed). Guard on
    started-but-not-completed so a prior completed turn's content is never
    mis-attributed to this interrupted one (A4 partial-output preservation).
    """
    stream = StreamingRuntimeState(context.metadata)
    if not stream.started() or stream.completed():
        return ""
    return (stream.content() or "").strip()


def _apply_redirect_correction(
    host: "LlmStepHost",
    context: RunContext,
    observations: Any,
    clarification: Any,
    text: str,
    *,
    queue_id: str | None = None,
) -> Any:
    """Fold a hard-redirect correction into the run + rebuild the request (030 B).

    Records a plain assistant checkpoint (role alternation stays valid — no signed
    reasoning is replayed) then the correction as a REAL user turn, frames it as a
    priority instruction via ``request_only_context`` (epic 026), and rebuilds the
    trimmed request. Emits a raw-free ``steering_redirect_applied`` signal.
    """
    from agent_driver.contracts.enums import ChatRole
    from agent_driver.contracts.messages import ChatMessage
    from agent_driver.contracts.scaffolding import scaffolding_metadata

    correction = (text or "").strip()
    run_input = context.run_input
    messages = list(run_input.messages)
    if queue_id is not None and any(
        message.metadata.get("live_message_queue_id") == queue_id
        for message in messages
    ):
        rebuilt, _ = _build_trimmed_request(
            host, context, observations, clarification
        )
        return _narrow_request_tools_to_forced_choice(rebuilt)
    # A4: preserve whatever the model had already streamed before the abort, so the
    # partial answer is not silently discarded — the model sees its own draft, then the
    # correction. Text only (no signed reasoning is replayed), so alternation stays valid.
    partial = _preserved_partial_output(context)
    interrupted_metadata = scaffolding_metadata("redirect_interrupt_checkpoint")
    if partial:
        interrupted_content = f"{partial}\n\n[Ответ прерван поправкой пользователя.]"
        interrupted_metadata = {
            **interrupted_metadata,
            "partial_output_preserved": True,
        }
        # Consume the buffer so a later abort in this step cannot re-attach the same draft.
        StreamingRuntimeState(context.metadata).mark_completed(partial)
    else:
        interrupted_content = "[Предыдущий ответ прерван поправкой пользователя.]"
    interrupted = ChatMessage(
        role=ChatRole.ASSISTANT,
        content=interrupted_content,
        metadata=interrupted_metadata,
    )
    messages.append(interrupted)
    # The correction itself is a GENUINE user turn (epic 030) — never tagged.
    corrected = ChatMessage(
        role=ChatRole.USER,
        content=correction,
        metadata=(
            {"live_message_queue_id": queue_id} if queue_id is not None else {}
        ),
    )
    messages.append(corrected)
    frame = redirect_correction_frame()
    context.run_input = run_input.model_copy(
        update={
            "input": correction,
            "messages": messages,
            "request_only_context": [*run_input.request_only_context, frame],
        }
    )
    protocol = context.metadata.get("protocol_messages")
    if isinstance(protocol, list):
        protocol.extend(
            [
                interrupted.model_dump(mode="json"),
                corrected.model_dump(mode="json"),
            ]
        )
        context.metadata["protocol_messages"] = protocol
    count = int(context.metadata.get("redirect_count_step", 0) or 0) + 1
    context.metadata["redirect_count_step"] = count
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "signal_id": "steering_redirect_applied",
            "severity": "info",
            "redirect_count_step": count,
            "partial_output_preserved": bool(partial),
            "partial_output_chars": len(partial),
            "raw_free": True,
        },
    )
    rebuilt, _ = _build_trimmed_request(host, context, observations, clarification)
    return _narrow_request_tools_to_forced_choice(rebuilt)


def _handle_provider_rejection(
    host: LlmStepHost,
    context: RunContext,
    request: Any,
    exc: httpx.HTTPStatusError,
) -> None:
    """Provider rejected the request (HTTP status error): emit LLM_REQUEST_REJECTED +
    RUN_FAILED with diagnostics and re-raise as a terminal execution error. A 400 is
    a protocol reason, everything else a model error."""
    reason = (
        TerminalReason.PROVIDER_PROTOCOL.value
        if exc.response.status_code == 400
        else TerminalReason.MODEL_ERROR.value
    )
    provider_message = _provider_error_message(exc.response)
    diagnostics = _provider_failure_diagnostics(
        host,
        context,
        request,
        exc,
        transition_reason=reason,
    )
    rejected_payload: dict[str, Any] = {
        "reason": reason,
        "status_code": exc.response.status_code,
        "provider_diagnostics": diagnostics,
    }
    if provider_message:
        rejected_payload["message"] = provider_message
    if debug_llm_payload_enabled():
        rejected_payload["request_stats"] = summarize_llm_request_payload(request)
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=RuntimeEventType.LLM_REQUEST_REJECTED,
            payload=rejected_payload,
        )
    )
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=RuntimeEventType.RUN_FAILED,
            payload={
                "reason": reason,
                "status_code": exc.response.status_code,
                "message": provider_message,
                "provider_diagnostics": diagnostics,
            },
        )
    )
    context.metadata["last_provider_error"] = reason
    context.metadata["last_provider_diagnostics"] = diagnostics
    raise RuntimeExecutionError("LLM completion failed") from exc


def _recover_or_fail_stream(
    host: LlmStepHost,
    context: RunContext,
    request: Any,
    exc: Exception,
    *,
    transition_reason: str,
) -> None:
    """Stream/provider error mid-completion: try to salvage a forced-final response;
    if none, tombstone the partial assistant turn, emit RUN_FAILED with diagnostics
    and re-raise. On successful recovery the response is stashed and the caller
    continues normally."""
    recovered = _recover_force_final_stream_response(
        host, context, reason=transition_reason
    )
    if recovered is not None:
        context.llm_response = recovered
        return
    _emit_partial_assistant_tombstone(host, context, reason=transition_reason)
    diagnostics = _provider_failure_diagnostics(
        host,
        context,
        request,
        exc,
        transition_reason=transition_reason,
    )
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=RuntimeEventType.RUN_FAILED,
            payload={
                "reason": TerminalReason.MODEL_ERROR.value,
                "transition_reason": transition_reason,
                "stream_diagnostics": diagnostics,
                "provider_diagnostics": diagnostics,
            },
        )
    )
    context.metadata["last_provider_error"] = transition_reason
    context.metadata["last_provider_stream_error"] = diagnostics
    raise RuntimeExecutionError("LLM completion failed") from exc


def _build_llm_completed_payload(context: RunContext) -> dict[str, Any]:
    """Assemble the LLM_CALL_COMPLETED event payload from the response + run metadata
    (provider/model/finish reason, duration, usage, planned/parsed tool-call
    forensics, provider/route profiles, effective tool names, planning snapshot)."""
    completed_payload: dict[str, Any] = {
        "provider": context.llm_response.provider,
        "model": context.llm_response.model,
        "finish_reason": context.llm_response.finish_reason.value,
    }
    started_at = context.metadata.get("llm_call_started_monotonic")
    if isinstance(started_at, (int, float)):
        completed_payload["duration_ms"] = round(
            max(0.0, (time.monotonic() - float(started_at)) * 1000.0),
            2,
        )
    if context.llm_response.usage is not None:
        completed_payload["usage"] = context.llm_response.usage.model_dump(mode="json")
    # Context occupancy (this step's pre-call pressure snapshot): the fraction of the
    # compaction trigger the prompt filled. Carried on every completion so a run trace
    # can tell a dormant compaction plane (occupancy always low) from a firing one —
    # even on runs that never crossed a pressure warning threshold.
    pressure_snapshot = context.metadata.get("token_pressure")
    if isinstance(pressure_snapshot, dict):
        occupancy = pressure_snapshot.get("occupancy_pct")
        if isinstance(occupancy, (int, float)):
            completed_payload["context_occupancy_pct"] = occupancy
    planned_tool_calls = context.llm_response.metadata.get("planned_tool_calls")
    if isinstance(planned_tool_calls, list):
        completed_payload["planned_tool_calls"] = planned_tool_calls
    tool_call_parse_errors = context.llm_response.metadata.get("tool_call_parse_errors")
    if isinstance(tool_call_parse_errors, list):
        completed_payload["tool_call_parse_errors"] = tool_call_parse_errors
    text_form_ranges = context.llm_response.metadata.get("text_form_tool_call_ranges")
    if isinstance(text_form_ranges, list):
        completed_payload["text_form_tool_call_ranges"] = text_form_ranges
    for flag in ("text_form_tool_calls_parsed", "text_form_tool_calls_suppressed"):
        if flag in context.llm_response.metadata:
            completed_payload[flag] = context.llm_response.metadata[flag]
    provider_profile = context.llm_response.metadata.get("provider_profile")
    if isinstance(provider_profile, dict):
        completed_payload["provider_profile"] = provider_profile
    route_profile = context.llm_response.metadata.get("route_profile")
    if isinstance(route_profile, dict):
        completed_payload["route_profile"] = route_profile
    provider_preflight = context.llm_response.metadata.get("provider_preflight")
    if isinstance(provider_preflight, dict):
        completed_payload["provider_preflight"] = provider_preflight
    provider_request_id = context.llm_response.metadata.get("provider_request_id")
    if isinstance(provider_request_id, str) and provider_request_id:
        completed_payload["provider_request_id"] = provider_request_id
    effective_tool_names = get_tool_loop_state(context).effective_tool_names()
    if effective_tool_names is not None:
        completed_payload["effective_tool_names"] = list(effective_tool_names)
    prompt_fragments = context.metadata.get("prompt_fragments")
    if isinstance(prompt_fragments, tuple):
        completed_payload["prompt_fragments"] = list(prompt_fragments)
    snapshot = build_planning_snapshot(context)
    if snapshot is not None:
        completed_payload["planning_snapshot"] = snapshot
    return completed_payload


def _run_user_text(run_input: Any) -> str:
    """The user's opening question for this run (for the async difficulty router)."""
    text = getattr(run_input, "input", None)
    if isinstance(text, str) and text.strip():
        return text
    for message in reversed(list(getattr(run_input, "messages", []) or [])):
        role = getattr(message, "role", None)
        role = role.value if hasattr(role, "value") else role
        if str(role) == "user":
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                return content
    return ""


async def _maybe_llm_route(host: "LlmStepHost", context: RunContext) -> None:
    """R8: drive an ASYNC model router (``aroute``) once per run and cache its verdict.

    A no-op unless a router with an ``aroute`` method is configured and the run hasn't
    been classified yet. The cached ``llm_routed_role`` is read by the sync build path
    (``pre_resolved_model_role``) for every turn, so the small classifier model is called
    exactly once per run. Any failure is swallowed — routing must never break a run.
    """
    if context.metadata.get("llm_routed_role"):
        return
    router = getattr(host._config, "model_router", None)
    aroute = getattr(router, "aroute", None)
    if aroute is None:
        return
    text = _run_user_text(context.run_input)
    if not text.strip():
        return
    from agent_driver.llm.model_router import RouteContext

    default_role = context.run_input.model_role or "default"
    try:
        role = await aroute(
            RouteContext(
                messages=[{"role": "user", "content": text}],
                run_input=context.run_input,
                default_role=default_role,
                step_index=context.llm_step_count,
            )
        )
    except Exception:  # noqa: BLE001 — routing must never break a run
        return
    if isinstance(role, str) and role:
        context.metadata["llm_routed_role"] = role


async def execute_llm_call_step(
    host: LlmStepHost, context: RunContext
) -> RuntimeStepResult:
    """Run LLM call step with trimming, compaction, and provider completion."""
    tool_state = get_tool_loop_state(context)
    context.metadata["llm_call_started_monotonic"] = time.monotonic()
    clarification = get_planning_runtime_state(context).clarification()
    try:
        observations = _microcompact_context_observations(host, context)
        # R8: async LLM router — classify the run's difficulty ONCE (first step) with a
        # cheap model and cache the role; the sync build path then reuses it every turn.
        await _maybe_llm_route(host, context)
        request, trim_payload = _build_trimmed_request(
            host, context, observations, clarification
        )
        request = _narrow_request_tools_to_forced_choice(request)
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.LLM_CALL_STARTED,
            payload={
                "provider": host._deps.provider.name,
                "tool_choice_effective": request.tool_choice,
                "request_allowed_tools": context.metadata.get(
                    "llm_request_allowed_tools"
                ),
                "request_tool_names": [
                    name
                    for name in (_request_tool_name(tool) for tool in request.tools)
                    if name
                ],
                "force_final_reason": tool_state.force_final_answer_reason(),
                "continuation_reason": context.metadata.get(
                    "continuation_nudge_reason"
                ),
            },
        )
        _emit_protocol_debug(host, context, request)
        compaction_state = get_compaction_runtime_state(context)
        compaction_state.set_trim_payload(trim_payload)
        token_state = compaction_state.token_pressure_state()
        request = _request_with_context_pressure_nudge(request, token_state)
        await apply_compaction_if_eligible(
            host,
            context=context,
            request=request,
            token_pressure_state=token_state,
        )
        # Per-call hook seam: let lifecycle hooks transform the finalized
        # request (inject prompt, filter tools, evict messages) before it ships.
        request = await dispatch_before_llm(
            host._deps.lifecycle_hooks, context, request
        )
        # OpenInference LLM span — Phoenix renders this as a colored LLM span with
        # the model, prompt/completion token counts (→ cost), the input/output
        # messages, and (on provider error) a red status. No-op when tracing off.
        from agent_driver.observability.openinference import (  # noqa: PLC0415
            SPAN_KIND_LLM,
            oi_span,
            record_status,
            set_io,
            set_llm,
        )

        _span_name = (
            f"llm {request.model}" if getattr(request, "model", None) else "llm"
        )
        with oi_span(_span_name, kind=SPAN_KIND_LLM) as _llm_span:
            _in_msgs = _messages_for_span(getattr(request, "messages", None))
            set_llm(
                _llm_span,
                model=getattr(request, "model", None),
                invocation_parameters={
                    "temperature": getattr(request, "temperature", None),
                    "max_tokens": getattr(request, "max_tokens", None),
                    "tool_choice": getattr(request, "tool_choice", None),
                },
                input_messages=_in_msgs,
            )
            set_io(_llm_span, input=_in_msgs)
            # Epic 025: liveness heartbeat while the provider call is in flight —
            # a long queue/TTFT wait is otherwise a silent stage under a frozen label.
            from agent_driver.runtime.single_agent.lifecycle.events import (  # noqa: PLC0415
                stage_wait_heartbeat,
            )

            async with stage_wait_heartbeat(
                host,
                context,
                stage="llm_completion",
                interval=getattr(host._config, "stage_heartbeat_seconds", None),
            ):
                # Epic 030 B: a hard redirect aborts THIS request mid-flight; apply
                # the correction as a real user turn and re-ask. Bounded to 2
                # redirects/step (anti-storm) — beyond that the correction is left
                # queued for the next step.
                for _redirect_attempt in range(3):
                    try:
                        context.llm_response = await _complete_request(
                            host,
                            context,
                            request,
                            recover_context_overflow=_overflow_recovery(
                                host, context, request, clarification
                            ),
                        )
                        break
                    except _RedirectRequested as _redirect:
                        # Abort applied; fold the correction into a real user turn
                        # and rebuild the request (host clears the probe after one
                        # read, so the re-ask completes normally).
                        request = _apply_redirect_correction(
                            host,
                            context,
                            observations,
                            clarification,
                            _redirect.text,
                            queue_id=(
                                _redirect.item.queue_id
                                if _redirect.item is not None
                                else None
                            ),
                        )
                        if _redirect.item is not None:
                            host._save_checkpoint(
                                context,
                                latest_output=None,
                                node_id="llm_redirect",
                            )
                            mark_applied = getattr(
                                host._deps.command_queue_store,
                                "mark_applied",
                                None,
                            )
                            if callable(mark_applied):
                                applied = mark_applied(
                                    _redirect.item.queue_id,
                                    claimant_id=_redirect.claimant_id,
                                    applied_phase=LiveMessagePhase.LLM_IN_FLIGHT,
                                )
                                if applied is not None:
                                    emit_step_event(
                                        host,
                                        context,
                                        event_type=live_message_transition_event(
                                            applied
                                        ),
                                        payload=live_message_receipt(applied),
                                    )

            _resp = context.llm_response
            _usage = getattr(_resp, "usage", None)
            _out_msg = getattr(_resp, "message", None)
            _out_content = _content_for_span(_out_msg) if _out_msg is not None else None
            set_llm(
                _llm_span,
                model=getattr(_resp, "model", None) or getattr(request, "model", None),
                provider=getattr(_resp, "provider", None),
                output_messages=(
                    [
                        {
                            "role": str(getattr(_out_msg, "role", "assistant")),
                            "content": _out_content,
                        }
                    ]
                    if _out_msg is not None
                    else None
                ),
                prompt_tokens=getattr(_usage, "input_tokens", None),
                completion_tokens=getattr(_usage, "output_tokens", None),
                total_tokens=getattr(_usage, "total_tokens", None),
            )
            set_io(_llm_span, output=_out_content)
            record_status(_llm_span, ok=True)
        if _usage is not None:
            # Fold this call's tokens/cost into the run ledger so the budget
            # gate (_terminal_from_limits) can fail fast when exceeded.
            get_cost_runtime_state(context).accumulate(_usage)
            # BUG-6: calibrate chars/token from the provider's ACTUAL input count vs
            # the chars we estimated we sent, so the next turn's pressure/budget
            # estimate is content-accurate (EMA, clamped; a bad datapoint is ignored).
            _pressure_snapshot = context.metadata.get("token_pressure")
            _chars_sent = (
                _pressure_snapshot.get("total_chars")
                if isinstance(_pressure_snapshot, dict)
                else None
            )
            if isinstance(_chars_sent, int):
                context.metadata["context_chars_per_token"] = calibrate_chars_per_token(
                    float(
                        context.metadata.get(
                            "context_chars_per_token", DEFAULT_CHARS_PER_TOKEN
                        )
                    ),
                    chars_sent=_chars_sent,
                    actual_input_tokens=getattr(_usage, "input_tokens", None),
                )
            # Epic 028 phase E: cache-break forensics (no-op when the provider
            # reports no cache fields — honesty over fabricated verdicts).
            from agent_driver.runtime.single_agent.llm_step.cache_forensics import (  # noqa: PLC0415
                check_prompt_cache_break,
            )

            check_prompt_cache_break(host, context, request, _usage)
        if context.llm_response is not None:
            await dispatch_after_llm(
                host._deps.lifecycle_hooks, context, context.llm_response
            )
    except httpx.HTTPStatusError as exc:
        _handle_provider_rejection(host, context, request, exc)
    except httpx.HTTPError as exc:
        transition_reason = (
            "stream_idle_timeout"
            if isinstance(exc, LlmStreamIdleTimeout)
            else TerminalReason.MODEL_ERROR.value
        )
        _recover_or_fail_stream(
            host, context, request, exc, transition_reason=transition_reason
        )
    except (RuntimeError, ValueError) as exc:
        _recover_or_fail_stream(
            host, context, request, exc, transition_reason="provider_stream_error"
        )
    token_chunks = context.llm_response.metadata.get("token_chunks")
    if isinstance(token_chunks, list) and not bool(
        context.llm_response.metadata.get("token_chunks_emitted")
    ):
        emit_token_delta_events(
            host,
            context,
            [chunk for chunk in token_chunks if isinstance(chunk, str)],
        )
    completed_payload = _build_llm_completed_payload(context)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.LLM_CALL_COMPLETED,
        payload=completed_payload,
    )
    _emit_token_pressure_warning(host, context)
    context.step_count += 1
    context.llm_step_count += 1
    context.metadata["last_llm_response"] = context.llm_response.model_dump(mode="json")
    get_loop_control_state(context).set_llm_step_transition(
        tool_calls=context.tool_calls
    )
    host._save_checkpoint(context, latest_output=None, node_id="llm_call")
    host._maybe_fail_after_step("llm_call")
    return RuntimeStepResult(next_step="tool_stage")


__all__ = [
    "LlmStepHost",
    "_complete_request",
    "_effective_code_agent_imports",
    "_force_final_answer_message",
    "_react_system_instruction",
    "_retry_forced_final_without_tools",
    "_runtime_attachment_messages",
    "execute_llm_call_step",
]
