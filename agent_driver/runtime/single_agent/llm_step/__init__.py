"""LLM call step for single-agent runtime."""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from agent_driver.contracts.enums import (
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.llm.payload_debug import (
    debug_llm_payload_enabled,
    summarize_llm_request_payload,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.metadata_state import (
    get_compaction_runtime_state,
    get_loop_control_state,
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    CompactionStageHost,
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.llm_step.completion import (
    complete_request as _complete_request,
    retry_forced_final_without_tools as _retry_forced_final_without_tools,
)
from agent_driver.runtime.single_agent.llm_step.context_pressure import (
    emit_token_pressure_warning as _emit_token_pressure_warning,
    request_with_context_pressure_nudge as _request_with_context_pressure_nudge,
)
from agent_driver.runtime.single_agent.llm_step.provider_requests import (
    narrow_request_tools_to_forced_choice as _narrow_request_tools_to_forced_choice,
    provider_error_message as _provider_error_message,
    request_tool_name as _request_tool_name,
)
from agent_driver.runtime.single_agent.llm_step.prompt import (
    effective_code_agent_imports as _effective_code_agent_imports,
    react_system_instruction as _react_system_instruction,
    runtime_attachment_messages,
)
from agent_driver.runtime.single_agent.llm_step.request import (
    build_trimmed_request as _build_trimmed_request,
    emit_protocol_debug as _emit_protocol_debug,
    microcompact_context_observations as _microcompact_context_observations,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    emit_partial_assistant_tombstone as _emit_partial_assistant_tombstone,
    force_final_answer_message as _force_final_answer_message,
    recover_force_final_stream_response as _recover_force_final_stream_response,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.planning.state import build_planning_snapshot
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmStreamIdleTimeout,
    emit_token_delta_events,
)
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


async def execute_llm_call_step(
    host: LlmStepHost, context: RunContext
) -> RuntimeStepResult:
    """Run LLM call step with trimming, compaction, and provider completion."""
    tool_state = get_tool_loop_state(context)
    context.metadata["llm_call_started_monotonic"] = time.monotonic()
    clarification = get_planning_runtime_state(context).clarification()
    try:
        observations = _microcompact_context_observations(host, context)
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
        # OpenInference LLM span — Phoenix renders this as a colored LLM span with
        # the model, prompt/completion token counts (→ cost), the input/output
        # messages, and (on provider error) a red status. No-op when tracing off.
        from agent_driver.observability.openinference import (  # noqa: PLC0415
            oi_span,
            record_status,
            set_io,
            set_llm,
            SPAN_KIND_LLM,
        )

        _span_name = f"llm {request.model}" if getattr(request, "model", None) else "llm"
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
            context.llm_response = await _complete_request(host, context, request)
            _resp = context.llm_response
            _usage = getattr(_resp, "usage", None)
            _out_msg = getattr(_resp, "message", None)
            _out_content = _content_for_span(_out_msg) if _out_msg is not None else None
            set_llm(
                _llm_span,
                model=getattr(_resp, "model", None) or getattr(request, "model", None),
                provider=getattr(_resp, "provider", None),
                output_messages=(
                    [{"role": str(getattr(_out_msg, "role", "assistant")), "content": _out_content}]
                    if _out_msg is not None
                    else None
                ),
                prompt_tokens=getattr(_usage, "input_tokens", None),
                completion_tokens=getattr(_usage, "output_tokens", None),
                total_tokens=getattr(_usage, "total_tokens", None),
            )
            set_io(_llm_span, output=_out_content)
            record_status(_llm_span, ok=True)
    except httpx.HTTPStatusError as exc:
        reason = (
            TerminalReason.PROVIDER_PROTOCOL.value
            if exc.response.status_code == 400
            else TerminalReason.MODEL_ERROR.value
        )
        provider_message = _provider_error_message(exc.response)
        rejected_payload: dict[str, Any] = {
            "reason": reason,
            "status_code": exc.response.status_code,
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
                },
            )
        )
        context.metadata["last_provider_error"] = reason
        raise RuntimeExecutionError("LLM completion failed") from exc
    except httpx.HTTPError as exc:
        transition_reason = (
            "stream_idle_timeout"
            if isinstance(exc, LlmStreamIdleTimeout)
            else TerminalReason.MODEL_ERROR.value
        )
        recovered = _recover_force_final_stream_response(
            host, context, reason=transition_reason
        )
        if recovered is not None:
            context.llm_response = recovered
        else:
            _emit_partial_assistant_tombstone(host, context, reason=transition_reason)
            host._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_FAILED,
                    payload={
                        "reason": TerminalReason.MODEL_ERROR.value,
                        "transition_reason": transition_reason,
                    },
                )
            )
            context.metadata["last_provider_error"] = transition_reason
            raise RuntimeExecutionError("LLM completion failed") from exc
    except (RuntimeError, ValueError) as exc:
        transition_reason = "provider_stream_error"
        recovered = _recover_force_final_stream_response(
            host, context, reason=transition_reason
        )
        if recovered is not None:
            context.llm_response = recovered
        else:
            _emit_partial_assistant_tombstone(host, context, reason=transition_reason)
            host._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_FAILED,
                    payload={
                        "reason": TerminalReason.MODEL_ERROR.value,
                        "transition_reason": transition_reason,
                    },
                )
            )
            raise RuntimeExecutionError("LLM completion failed") from exc
    token_chunks = context.llm_response.metadata.get("token_chunks")
    if isinstance(token_chunks, list) and not bool(
        context.llm_response.metadata.get("token_chunks_emitted")
    ):
        emit_token_delta_events(
            host,
            context,
            [chunk for chunk in token_chunks if isinstance(chunk, str)],
        )
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
    planned_tool_calls = context.llm_response.metadata.get("planned_tool_calls")
    if isinstance(planned_tool_calls, list):
        completed_payload["planned_tool_calls"] = planned_tool_calls
    provider_profile = context.llm_response.metadata.get("provider_profile")
    if isinstance(provider_profile, dict):
        completed_payload["provider_profile"] = provider_profile
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
