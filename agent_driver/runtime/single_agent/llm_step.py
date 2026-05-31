"""LLM call step for single-agent runtime."""

from __future__ import annotations

import json
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from agent_driver.context import (
    microcompact_observations,
    render_planning_step_prompt,
)
from agent_driver.contracts.context import PlanningStep
from agent_driver.contracts.enums import (
    AgentProfile,
    ChatRole,
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.payload_debug import (
    debug_llm_payload_enabled,
    summarize_llm_request_payload,
)
from agent_driver.prompts import (
    force_final_answer_user_message,
    python_tool_system_addendum,
    react_base_policy,
    react_chat_tool_policy,
    react_chat_tool_policy_fragment_names,
    todo_write_guidance,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.skills import CURATED_RESEARCH_SKILL_NAMES, curated_skills_dir
from agent_driver.runtime.metadata_state import (
    get_compaction_runtime_state,
    get_loop_control_state,
    get_planning_runtime_state,
    get_research_runtime_state,
    get_streaming_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_evidence import RESEARCH_DEPTH_SOURCE_VERIFIED
from agent_driver.runtime.single_agent.compaction_stage import (
    CompactionStageHost,
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.llm import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
    effective_tool_names_from_registry,
)
from agent_driver.runtime.single_agent.llm_step_context_pressure import (
    emit_token_pressure_warning as _emit_token_pressure_warning,
    request_with_context_pressure_nudge as _request_with_context_pressure_nudge,
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
from agent_driver.runtime.task_contract import render_task_contract_reminder


class LlmStepHost(CompactionStageHost, Protocol):
    """Host surface for LLM step execution."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: EventSpec) -> None: ...
    def _save_checkpoint(
        self, context: RunContext, *, latest_output: Any, node_id: str
    ) -> Any: ...
    def _maybe_fail_after_step(self, step_name: str) -> None: ...


def _force_final_answer_message(context: RunContext) -> str:
    message = force_final_answer_user_message()
    source_links = _fetched_source_links(context)
    if not source_links:
        return message
    bullets = "\n".join(f"- {title}: {url}" for title, url in source_links[:5])
    return (
        f"{message}\n\n"
        "You used fetched web sources. Include concrete Markdown links in the "
        "final answer and base the synthesis on these URLs:\n"
        f"{bullets}"
    )


def _fetched_source_links(context: RunContext) -> list[tuple[str, str]]:
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if not (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth") == RESEARCH_DEPTH_SOURCE_VERIFIED
    ):
        return []
    tool_results = get_tool_loop_state(context).tool_results()
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict) or call.get("tool_name") != "web_fetch":
            continue
        url = _tool_result_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append((_source_label(item, url), url))
    return links


def _tool_result_url(item: dict[str, Any]) -> str | None:
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        url = structured.get("url")
        if isinstance(url, str) and url:
            return url
    call = item.get("call")
    if isinstance(call, dict):
        args = call.get("args")
        if isinstance(args, dict):
            url = args.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def _source_label(item: dict[str, Any], url: str) -> str:
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        metadata = structured.get("metadata")
        if isinstance(metadata, dict):
            title = metadata.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
    domain = urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain or "source"


def _emit_partial_assistant_tombstone(
    host: LlmStepHost,
    context: RunContext,
    *,
    reason: str,
) -> None:
    """Mark partial streamed assistant output as invalid before terminal failure."""
    streaming_state = get_streaming_runtime_state(context)
    if not streaming_state.started():
        return
    if streaming_state.completed():
        return
    content = streaming_state.content()
    if not isinstance(content, str) or not content:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED,
        payload={
            "reason": reason,
            "content": content,
            "transition_reason": "partial_tombstone",
        },
    )
    streaming_state.mark_tombstoned()


def _recover_force_final_stream_response(
    host: LlmStepHost,
    context: RunContext,
    *,
    reason: str,
) -> LlmResponse | None:
    """Preserve a late-stream final answer when provider transport drops.

    OpenAI-compatible streaming providers can occasionally fail after a long
    final-answer delta was already emitted. When runtime itself forced a final
    answer, keeping that text is better than tombstoning a useful sourced
    report. Early/short partials still fail normally.
    """
    streaming_state = get_streaming_runtime_state(context)
    if not get_tool_loop_state(context).force_final_answer_enabled():
        return None
    if streaming_state.completed():
        return None
    content = streaming_state.content()
    if not isinstance(content, str) or len(content.strip()) < 200:
        return None
    streaming_state.mark_recovered(content=content, reason=reason)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": "Recovered partial final answer after provider stream error.",
            "signal_id": "provider_stream_partial_final_recovered",
            "severity": "warning",
            "transition_reason": reason,
            "chars": len(content),
        },
    )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED,
        payload={
            "content": content,
            "finish_reason": LlmFinishReason.UNKNOWN.value,
            "provider": host._deps.provider.name,
            "model": "stream-model",
            "recovered_partial": True,
            "transition_reason": reason,
        },
    )
    return LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=content),
        finish_reason=LlmFinishReason.UNKNOWN,
        provider=host._deps.provider.name,
        model="stream-model",
        metadata={
            "token_chunks_emitted": True,
            "provider_stream_partial_final_recovered": True,
            "transition_reason": reason,
        },
    )


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


def _provider_error_message(response: httpx.Response) -> str:
    """Extract a short provider-facing error message from an HTTP response."""
    body = response.text.strip()
    if not body:
        return f"Provider rejected the request with HTTP {response.status_code}."
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    if not isinstance(payload, dict):
        return body[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    for key in ("message", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return body[:500]


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


def _is_invalid_encrypted_reasoning_error(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code != 400:
        return False
    body = exc.response.text.lower()
    return (
        "invalid_encrypted_content" in body
        or "encrypted content" in body
        and "could not be" in body
    )


def _is_forced_tool_choice_provider_error(
    exc: httpx.HTTPStatusError,
    request: Any,
) -> bool:
    if exc.response.status_code not in {400, 404}:
        return False
    if not isinstance(request, LlmRequest):
        return False
    return _forced_named_tool_choice(request.tool_choice) is not None


def _forced_named_tool_choice(tool_choice: object) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") != "tool":
        return None
    name = tool_choice.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _narrow_request_tools_to_forced_choice(request: Any) -> Any:
    if not isinstance(request, LlmRequest):
        return request
    forced_tool_name = _forced_named_tool_choice(request.tool_choice)
    if not forced_tool_name:
        return request
    tools = _request_tools_matching(request.tools, forced_tool_name)
    if not tools or len(tools) == len(request.tools):
        return request
    metadata = dict(request.metadata)
    metadata["forced_tool_catalog"] = forced_tool_name
    return request.model_copy(update={"metadata": metadata, "tools": tools})


def _request_without_forced_tool_choice(request: LlmRequest) -> LlmRequest:
    forced_tool_name = _forced_named_tool_choice(request.tool_choice)
    metadata = dict(request.metadata)
    metadata["forced_tool_choice_retry"] = "removed_after_provider_rejection"
    tools = request.tools
    if forced_tool_name:
        tools = _request_tools_matching(request.tools, forced_tool_name)
    return request.model_copy(
        update={
            "metadata": metadata,
            "tools": tools,
            "tool_choice": None,
        }
    )


def _request_tools_matching(
    tools: list[dict[str, Any]],
    tool_name: str,
) -> list[dict[str, Any]]:
    return [tool for tool in tools if _request_tool_name(tool) == tool_name]


def _request_tool_name(tool: object) -> str | None:
    if not isinstance(tool, dict):
        return None
    function = tool.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _should_retry_empty_forced_final_non_stream(
    context: RunContext, response: LlmResponse
) -> bool:
    metadata = getattr(context, "metadata", {})
    if not isinstance(metadata, dict) or metadata.get("force_final_answer") is not True:
        return False
    if response.finish_reason != LlmFinishReason.STOP:
        return False
    if (response.message.content or "").strip():
        return False
    planned = response.metadata.get("planned_tool_calls")
    return not isinstance(planned, list) or not planned


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


def _forced_final_no_tools_retry_reason(
    context: RunContext,
    request: Any,
    response: LlmResponse,
) -> str | None:
    metadata = getattr(context, "metadata", {})
    if not isinstance(metadata, dict) or metadata.get("force_final_answer") is not True:
        return None
    if not isinstance(request, LlmRequest):
        return None
    if not request.tools and request.tool_choice is None:
        return None
    if response.metadata.get("text_form_tool_calls_suppressed") is True:
        return "tool_call"
    planned = response.metadata.get("planned_tool_calls")
    if isinstance(planned, list) and planned:
        return "tool_call"
    if response.finish_reason != LlmFinishReason.STOP:
        return None
    if (response.message.content or "").strip():
        return None
    return "empty" if not isinstance(planned, list) or not planned else None


def _request_without_tools(
    request: LlmRequest, *, provider_name: str | None = None
) -> LlmRequest:
    messages = [
        *request.messages,
        ChatMessage(
            role=ChatRole.USER,
            content=(
                "Final answer retry: the previous forced-final attempt returned "
                "empty content. Tools are now disabled. Return the final answer "
                "now, in the user's language, using only the evidence and tool "
                "results already present in this conversation. Do not mention "
                "tool limitations; include source links when the task used web "
                "sources."
            ),
            metadata={"runtime_retry": "empty_forced_final_no_tools"},
        ),
    ]
    metadata = dict(request.metadata)
    if _should_disable_reasoning_for_no_tools_retry(
        request=request,
        provider_name=provider_name,
    ):
        provider_extra_body = dict(metadata.get("provider_extra_body") or {})
        provider_extra_body["reasoning"] = {"enabled": False, "exclude": True}
        metadata["provider_extra_body"] = provider_extra_body
    return request.model_copy(
        update={
            "messages": messages,
            "metadata": metadata,
            "stream": False,
            "tools": [],
            "tool_choice": None,
            "parallel_tool_calls": None,
        }
    )


def _should_disable_reasoning_for_no_tools_retry(
    *, request: LlmRequest, provider_name: str | None
) -> bool:
    provider_l = (provider_name or "").strip().lower()
    model_l = (request.model or "").strip().lower()
    return provider_l == "openrouter" and "deepseek" in model_l


def _emit_non_stream_retry_assistant_message(
    host: LlmStepHost,
    context: RunContext,
    response: LlmResponse,
) -> None:
    content = (response.message.content or "").strip()
    if not content:
        return
    emit_token_delta_events(host, context, [content])
    get_streaming_runtime_state(context).mark_completed(content)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_REPLACED,
        payload={
            "content": content,
            "finish_reason": response.finish_reason.value,
            "provider": response.provider,
            "model": response.model,
            "replacement_reason": "empty_forced_final_no_tools_retry",
        },
    )


def _strip_reasoning_echo(request: Any) -> Any:
    if not isinstance(request, LlmRequest):
        return request
    changed = False
    messages: list[ChatMessage] = []
    for message in request.messages:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if "reasoning_details" not in metadata and "reasoning" not in metadata:
            messages.append(message)
            continue
        updated_metadata = dict(metadata)
        updated_metadata.pop("reasoning_details", None)
        updated_metadata.pop("reasoning", None)
        messages.append(message.model_copy(update={"metadata": updated_metadata}))
        changed = True
    if not changed:
        return request
    return request.model_copy(update={"messages": messages})


def _is_reduce_max_tokens_credit_error(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code != 402:
        return False
    body = exc.response.text.lower()
    return "fewer max_tokens" in body or "requested up to" in body


def _request_with_reduced_max_tokens(request: Any) -> Any:
    if not isinstance(request, LlmRequest):
        return request
    current = request.max_tokens if request.max_tokens is not None else 4096
    reduced = max(512, min(2048, int(current) // 2))
    if request.max_tokens == reduced:
        return request
    return request.model_copy(update={"max_tokens": reduced})


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


def _append_runtime_attachment_messages(
    context: RunContext,
    protocol_messages: tuple[ChatMessage, ...] | None,
) -> tuple[ChatMessage, ...] | None:
    attachments = _runtime_attachment_messages(context)
    if not attachments:
        return protocol_messages
    if protocol_messages is not None:
        return protocol_messages + attachments
    base_messages = tuple(context.run_input.messages)
    if not base_messages:
        content = str(context.run_input.input or "").strip()
        if content:
            base_messages = (ChatMessage(role=ChatRole.USER, content=content),)
    return base_messages + attachments if base_messages else attachments


def _runtime_attachment_messages(context: RunContext) -> tuple[ChatMessage, ...]:
    """Return volatile model-facing chat reminders as request attachments."""
    if context.run_input.app_metadata.get("chat_mode") is not True:
        return tuple()
    lines = _chat_mode_runtime_reminders(context)
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if isinstance(task_contract, dict):
        reminder = render_task_contract_reminder(task_contract)
        if reminder:
            lines.append(reminder)
    planning_hint = context.run_input.tool_policy.metadata.get("planning_hint")
    if isinstance(planning_hint, dict):
        level = str(planning_hint.get("level") or "")
        reason = str(planning_hint.get("reason") or "").strip()
        if level == "suggested":
            lines.append(
                "Planning hint: this request looks like non-trivial "
                "implementation work; prefer enter_plan_mode before execution. "
                f"Reason: {reason or 'adaptive planning suggested'}."
            )
        elif level == "required":
            lines.append(
                "Planning hint: approved planning is required before "
                "side-effecting execution. Enter plan mode and call "
                "exit_plan_mode_v2 with concrete plan content."
            )
    return tuple(
        ChatMessage(
            role=ChatRole.USER,
            content=line,
            metadata={"kind": "runtime_attachment"},
        )
        for line in lines
        if line.strip()
    )


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


def _effective_code_agent_imports(host: LlmStepHost) -> tuple[str, ...]:
    imports = host._config.authorized_imports
    if imports:
        return imports
    if host._config.python_tool.enabled:
        from agent_driver.tools.builtin.python_imports import effective_python_imports

        return effective_python_imports(host._config.python_tool)
    return tuple()


def _effective_request_tool_names(
    host: LlmStepHost, context: RunContext
) -> tuple[str, ...]:
    policy = context.run_input.tool_policy
    return effective_tool_names_from_registry(
        host._deps.tool_registry,
        allowed=(
            tuple(policy.allowed_tools) if policy.allowed_tools is not None else None
        ),
        denied=tuple(policy.denied_tools) if policy.denied_tools else None,
    )


def _python_tool_addendum_if_present(
    host: LlmStepHost, effective_tool_names: tuple[str, ...]
) -> str | None:
    if not host._config.python_tool.enabled:
        return None
    if "python" not in effective_tool_names:
        return None
    return python_tool_system_addendum(host._config.python_tool)


def _todo_write_guidance_if_present(
    effective_tool_names: tuple[str, ...],
) -> str | None:
    if "todo_write" not in effective_tool_names:
        return None
    return todo_write_guidance()


def _remember_prompt_surface(
    context: RunContext,
    *,
    effective_tool_names: tuple[str, ...],
    prompt_fragments: tuple[str, ...],
) -> None:
    metadata = getattr(context, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["effective_tool_names"] = effective_tool_names
    metadata["prompt_fragments"] = prompt_fragments


def _react_system_instruction(host: LlmStepHost, context: RunContext) -> str | None:
    if context.run_input.agent_profile != AgentProfile.REACT_TEXT:
        return None
    lines = [react_base_policy()]
    if context.run_input.app_metadata.get("chat_mode") is True:
        from agent_driver.tools.builtin.python_imports import scientific_imports_enabled

        effective_tool_names = _effective_request_tool_names(host, context)
        prompt_fragments = react_chat_tool_policy_fragment_names(effective_tool_names)
        _remember_prompt_surface(
            context,
            effective_tool_names=effective_tool_names,
            prompt_fragments=prompt_fragments,
        )
        lines.append(
            react_chat_tool_policy(
                include_scientific_python=scientific_imports_enabled(
                    host._config.python_tool
                ),
                available_tool_names=effective_tool_names,
            )
        )
    else:
        effective_tool_names = _effective_request_tool_names(host, context)
        _remember_prompt_surface(
            context,
            effective_tool_names=effective_tool_names,
            prompt_fragments=tuple(),
        )
    python_addendum = _python_tool_addendum_if_present(host, effective_tool_names)
    if python_addendum:
        lines.append(python_addendum)
    todo_guidance = _todo_write_guidance_if_present(effective_tool_names)
    if todo_guidance:
        lines.append(todo_guidance)
    workspace_cwd = context.run_input.app_metadata.get("workspace_cwd")
    if isinstance(workspace_cwd, str) and workspace_cwd.strip():
        lines.append(f"Workspace cwd: {workspace_cwd.strip()}")
    return "\n".join(lines)


def _chat_mode_runtime_reminders(context: RunContext) -> list[str]:
    """Return compact mode reminders for the chat ReAct prompt."""
    reminders: list[str] = []
    planning_payload = context.metadata.get("planning_state")
    deliverable_requested = _current_turn_requests_deliverable(context)
    deliverable_policy = context.run_input.tool_policy.metadata.get(
        "deliverable_request"
    )
    if (
        isinstance(deliverable_policy, dict)
        and deliverable_policy.get("enabled") is True
    ):
        reminders.append(
            "Runtime reminder: deliverable_request_active. The user asked for the "
            "answer/draft now; use tools only if needed for missing facts, then "
            "produce the final deliverable. Do not ask for plan approval or another "
            "clarification unless a safety requirement blocks progress."
        )
    python_policy = context.run_input.tool_policy.metadata.get(
        "python_reliability_request"
    )
    policy_allowed = context.run_input.tool_policy.allowed_tools
    policy_denied = context.run_input.tool_policy.denied_tools or []
    python_allowed_by_policy = "python" not in policy_denied and (
        policy_allowed is None or "python" in policy_allowed
    )
    if (
        isinstance(python_policy, dict)
        and python_policy.get("enabled") is True
        and python_allowed_by_policy
    ):
        reminders.append(
            "Runtime reminder: python_reliability_request. The current user turn "
            "asks for exact calculation/counting; call the python tool before the "
            "final answer, then answer naturally from the execution result."
        )
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth") == RESEARCH_DEPTH_SOURCE_VERIFIED
        and "web_fetch" not in policy_denied
        and (policy_allowed is None or "web_fetch" in policy_allowed)
    ):
        reminders.append(
            "Runtime reminder: source_verified_report. For report/deep-research "
            "work, search results are candidates, not evidence. Use web_fetch on "
            "multiple relevant URLs before final synthesis when URLs are available. "
            "When you synthesize the final answer from fetched web evidence, include "
            "Markdown links to the concrete fetched/source URLs you relied on."
        )
        if _tool_available_by_policy(
            name="skill_view",
            allowed=policy_allowed,
            denied=policy_denied,
        ):
            reminders.append(_research_skill_suggestion_message())
    if get_research_runtime_state(context).fetch_fallback_required():
        reminders.append(
            "Runtime reminder: research_fetch_fallback. Multiple page fetches "
            "failed; do not retry the same fetch loop. Answer from available "
            "search metadata and explicitly state that full pages could not be "
            "verified."
        )
    if get_planning_runtime_state(context).approved_plan():
        reminders.append(
            "Runtime reminder: planning_mode_exit. An approval plan has already "
            "been accepted for this run; continue execution instead of creating "
            "another approval plan."
        )
    if not isinstance(planning_payload, dict):
        return reminders
    todos = planning_payload.get("todos")
    planning_metadata = planning_payload.get("metadata")
    planning_mode = (
        planning_metadata.get("planning_mode")
        if isinstance(planning_metadata, dict)
        else None
    )
    if planning_mode == "plan":
        reminders.append(
            "Runtime reminder: planning_mode_active. Stay read-only, inspect and "
            "reason, ask only blocking questions, then call exit_plan_mode_v2 with "
            "a concrete approval-ready plan before side-effecting execution."
        )
    if isinstance(todos, list) and todos:
        if deliverable_requested:
            reminders.append(
                "Runtime reminder: deliverable_request_active_with_plan. A session "
                "checklist exists, but the current turn asks for the deliverable. "
                "Use existing context and produce the requested final answer in "
                "this turn; update todos only if needed, and do not restart "
                "planning, ask another clarification, or ask for plan approval."
            )
        else:
            reminders.append(
                "Runtime reminder: planning_mode_sparse. Session todos are active: "
                "follow existing todos, update statuses with todo_write "
                "(merge=true) as each step completes, keep moving to the next "
                "unfinished todo, and do not give a final answer until the "
                "requested plan is complete. Do not restate the full plan "
                "checklist in chat."
            )
    return reminders


def _tool_available_by_policy(
    *, name: str, allowed: list[str] | tuple[str, ...] | None, denied: list[str]
) -> bool:
    return name not in denied and (allowed is None or name in allowed)


def _research_skill_suggestion_message() -> str:
    names = ", ".join(CURATED_RESEARCH_SKILL_NAMES)
    return (
        "Runtime reminder: curated_research_skills_available. For "
        "source_verified_report work, relevant bundled skills may help: "
        f"{names}. Discover them with skill_tool using base_dir="
        f"{str(curated_skills_dir())!r}, then call skill_view for a selected "
        "skill before relying on it. Do not auto-load hidden instructions or "
        "treat skill listings as evidence."
    )


def _current_turn_requests_deliverable(context: RunContext) -> bool:
    current_input = str(context.run_input.input or "").lower()
    return any(
        marker in current_input
        for marker in (
            "write",
            "draft",
            "final",
            "deliverable",
            "напиши",
            "черновик",
            "итог",
            "финал",
            "не план",
        )
    )


__all__ = ["LlmStepHost", "execute_llm_call_step"]
