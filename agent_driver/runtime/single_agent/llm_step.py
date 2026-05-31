"""LLM call step for single-agent runtime."""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from agent_driver.context import (
    microcompact_observations,
    render_planning_step_prompt,
)
from agent_driver.contracts.context import PlanningStep
from agent_driver.contracts.enums import (
    ChatRole,
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmResponse
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
from agent_driver.runtime.single_agent.compaction_stage import (
    CompactionStageHost,
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.llm import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.runtime.single_agent.llm_step_context_pressure import (
    emit_token_pressure_warning as _emit_token_pressure_warning,
    request_with_context_pressure_nudge as _request_with_context_pressure_nudge,
)
from agent_driver.runtime.single_agent.llm_step_provider_requests import (
    is_forced_tool_choice_provider_error as _is_forced_tool_choice_provider_error,
    is_invalid_encrypted_reasoning_error as _is_invalid_encrypted_reasoning_error,
    is_reduce_max_tokens_credit_error as _is_reduce_max_tokens_credit_error,
    narrow_request_tools_to_forced_choice as _narrow_request_tools_to_forced_choice,
    provider_error_message as _provider_error_message,
    request_with_reduced_max_tokens as _request_with_reduced_max_tokens,
    request_without_forced_tool_choice as _request_without_forced_tool_choice,
    request_without_tools as _request_without_tools,
    strip_reasoning_echo as _strip_reasoning_echo,
)
from agent_driver.runtime.single_agent.llm_step_prompt import (
    append_runtime_attachment_messages as _append_runtime_attachment_messages,
    effective_code_agent_imports as _effective_code_agent_imports,
    react_system_instruction as _react_system_instruction,
    runtime_attachment_messages,
)
from agent_driver.runtime.single_agent.llm_step_stream_recovery import (
    emit_non_stream_retry_assistant_message as _emit_non_stream_retry_assistant_message,
    emit_partial_assistant_tombstone as _emit_partial_assistant_tombstone,
    force_final_answer_message as _force_final_answer_message,
    forced_final_no_tools_retry_reason as _forced_final_no_tools_retry_reason,
    recover_force_final_stream_response as _recover_force_final_stream_response,
    should_retry_empty_forced_final_non_stream as _should_retry_empty_forced_final_non_stream,
)
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.step_planning import build_planning_snapshot
from agent_driver.runtime.single_agent.streaming import (
    LlmStreamIdleTimeout,
    complete_streaming_request,
    emit_token_delta_events,
    is_stream_enabled,
)
from agent_driver.runtime.single_agent.todo_reminders import (
    maybe_append_todo_reminder_to_protocol,
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


async def execute_llm_call_step(
    host: LlmStepHost, context: RunContext
) -> RuntimeStepResult:
    """Run LLM call step with trimming, compaction, and provider completion."""
    tool_state = get_tool_loop_state(context)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.LLM_CALL_STARTED,
        payload={
            "provider": host._deps.provider.name,
            "tool_choice_effective": (
                tool_state.tool_choice_override()
                if tool_state.tool_choice_override() is not None
                else context.run_input.tool_choice
            ),
            "force_final_reason": tool_state.force_final_answer_reason(),
            "continuation_reason": context.metadata.get("continuation_nudge_reason"),
        },
    )
    context.metadata["llm_call_started_monotonic"] = time.monotonic()
    clarification = get_planning_runtime_state(context).clarification()
    try:
        observations = _microcompact_context_observations(host, context)
        request, trim_payload = _build_trimmed_request(
            host, context, observations, clarification
        )
        request = _narrow_request_tools_to_forced_choice(request)
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
        context.llm_response = await _complete_request(host, context, request)
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


def _microcompact_context_observations(
    host: LlmStepHost, context: RunContext
) -> list[dict[str, object]]:
    compaction_state = get_compaction_runtime_state(context)
    observations = compaction_state.observations()
    micro = microcompact_observations(
        [item for item in observations if isinstance(item, dict)],
        preserve_recent=host._config.microcompact_preserve_recent,
        max_preview_chars=host._config.microcompact_max_preview_chars,
    )
    compaction_state.set_microcompaction(
        observations=micro.observations,
        audit=micro.audit,
        bytes_saved=micro.bytes_saved,
        estimated_tokens_saved=micro.estimated_tokens_saved,
    )
    return micro.observations


def _build_trimmed_request(
    host: LlmStepHost,
    context: RunContext,
    observations: list[dict[str, object]],
    clarification: object,
) -> tuple[Any, dict[str, object]]:
    compaction_state = get_compaction_runtime_state(context)
    digest_refs = compaction_state.digest_refs()
    artifact_refs = compaction_state.artifact_refs()
    planning_prompt = None
    planning_step_payload = get_planning_runtime_state(context).planning_step()
    if host._config.include_planning_prompt and isinstance(planning_step_payload, dict):
        planning_prompt = render_planning_step_prompt(
            PlanningStep.model_validate(planning_step_payload)
        )
    protocol_messages = _protocol_messages_from_metadata(context)
    protocol_messages = _append_runtime_attachment_messages(
        context,
        protocol_messages,
    )
    protocol_messages = maybe_append_todo_reminder_to_protocol(
        context, protocol_messages
    )
    # Inner-loop overrides (e.g. ``"none"`` to force a final answer after a
    # repeated handler error) take precedence; otherwise fall through to
    # the caller-supplied ``RunInput.tool_choice`` so the public seam can
    # force a specific tool. None on both sides preserves the legacy
    # ``"auto"`` default applied by the provider adapters.
    tool_loop_state = get_tool_loop_state(context)
    tool_choice = tool_loop_state.tool_choice_override()
    if tool_choice is None:
        if (
            context.run_input.app_metadata.get("chat_mode") is True
            and context.llm_step_count > 0
        ):
            tool_choice = None
        else:
            tool_choice = context.run_input.tool_choice
    system_instruction = _react_system_instruction(host, context)
    if (
        tool_loop_state.force_final_answer_enabled()
        and protocol_messages is not None
        and protocol_messages
    ):
        protocol_messages = protocol_messages + (
            ChatMessage(
                role=ChatRole.USER,
                content=_force_final_answer_message(context),
            ),
        )
    return build_single_agent_llm_request(
        LlmRequestBuildContext(
            run_input=context.run_input,
            clarification=clarification if isinstance(clarification, str) else None,
            tool_docs=(
                context.metadata["code_tool_docs"]
                if isinstance(context.metadata.get("code_tool_docs"), str)
                else None
            ),
            authorized_imports=_effective_code_agent_imports(host),
            registry=host._deps.tool_registry,
            observations=(
                tuple()
                if protocol_messages is not None
                else tuple(item for item in observations if isinstance(item, dict))
            ),
            planning_prompt=planning_prompt,
            digest_ids=tuple(
                str(item.get("digest_id"))
                for item in digest_refs
                if isinstance(item, dict) and item.get("digest_id")
            ),
            artifact_ids=tuple(
                str(item.get("artifact_id"))
                for item in artifact_refs
                if isinstance(item, dict) and item.get("artifact_id")
            ),
            max_chars=host._config.trim_max_chars,
            max_messages=host._config.trim_max_messages,
            max_observations=host._config.trim_max_observations,
            context_window_estimate=host._config.context_window_estimate,
            warning_threshold=host._config.token_warning_threshold,
            compact_threshold=host._config.token_compact_threshold,
            blocking_threshold=host._config.token_blocking_threshold,
            output_token_reserve=host._config.output_token_reserve,
            stream=is_stream_enabled(context.run_input),
            system_instruction=system_instruction,
            protocol_messages=protocol_messages,
            tool_choice=(
                str(tool_choice)
                if isinstance(tool_choice, str)
                else (tool_choice if isinstance(tool_choice, dict) else None)
            ),
        )
    )


async def _complete_request(
    host: LlmStepHost, context: RunContext, request: Any
) -> LlmResponse:
    last_timeout: httpx.TimeoutException | None = None
    for attempt in range(3):
        try:
            if not is_stream_enabled(context.run_input):
                response = await host._deps.provider.complete(request)
                return await _retry_forced_final_without_tools(
                    host,
                    context,
                    request=request,
                    response=response,
                )
            response = await complete_streaming_request(host, context, request)
            if _should_retry_empty_forced_final_non_stream(context, response):
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
                return await _retry_forced_final_without_tools(
                    host,
                    context,
                    request=request,
                    response=response,
                )
            return response
        except httpx.HTTPStatusError as exc:
            if attempt == 0 and _is_invalid_encrypted_reasoning_error(exc):
                stripped = _strip_reasoning_echo(request)
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
            if _is_forced_tool_choice_provider_error(exc, request):
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
                request = _request_without_forced_tool_choice(request)
                continue
            if _is_reduce_max_tokens_credit_error(exc):
                reduced = _request_with_reduced_max_tokens(request)
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


async def _retry_forced_final_without_tools(
    host: LlmStepHost,
    context: RunContext,
    *,
    request: Any,
    response: LlmResponse,
) -> LlmResponse:
    retry_reason = _forced_final_no_tools_retry_reason(context, request, response)
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
        _request_without_tools(request, provider_name=provider_name)
    )
    _emit_non_stream_retry_assistant_message(host, context, retry_response)
    return retry_response


def _protocol_messages_from_metadata(
    context: RunContext,
) -> tuple[ChatMessage, ...] | None:
    payload = context.metadata.get("protocol_messages")
    if not isinstance(payload, list):
        return None
    rows: list[ChatMessage] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(ChatMessage.model_validate(item))
    return tuple(rows) if rows else None


def _emit_protocol_debug(host: LlmStepHost, context: RunContext, request: Any) -> None:
    if context.run_input.app_metadata.get("debug_tool_protocol") is not True:
        return
    messages = request.messages if isinstance(request.messages, list) else []
    roles = [message.role.value for message in messages]
    tool_names: list[str] = []
    for tool in request.tools:
        function_payload = tool.get("function")
        if isinstance(function_payload, dict):
            name = function_payload.get("name")
            if isinstance(name, str) and name.strip():
                tool_names.append(name)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "kind": "tool_protocol_debug",
            "message_count": len(messages),
            "roles": roles,
            "tool_names": tool_names,
            "tool_choice": request.tool_choice,
        },
    )


__all__ = ["LlmStepHost", "execute_llm_call_step"]
