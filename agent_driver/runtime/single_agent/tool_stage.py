"""Tool stage execution and transitions."""

from __future__ import annotations

import json
from typing import Any, Protocol

from agent_driver.contracts.enums import AgentProfile, ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason
from agent_driver.llm.tool_call_parser import strip_text_form_tool_calls
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.single_agent.pending import (
    pending_interrupt_from_execution_result,
    serialize_pending_interrupt,
)
from agent_driver.runtime.single_agent.step_observations import (
    build_observations_from_tool_result,
)
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.step_planning import (
    apply_planning_updates_from_envelopes,
    build_planning_snapshot,
    update_planning_state_from_tool_results,
)
from agent_driver.runtime.single_agent.todo_reminders import (
    append_todo_progress_hint_after_substantive_tool,
    increment_tool_loops_since_todo_write,
)
from agent_driver.prompts import force_final_answer_tool_message
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
    RuntimeStepResult,
)
from agent_driver.runtime.tools import ToolExecutionResult
from agent_driver.tools.executor.planned import extract_planned_tool_calls


class ToolStageHost(Protocol):
    """Host surface for tool stage execution."""

    _deps: RunnerDeps
    _config: RunnerConfig

    async def _tool_result_with_approved_override(
        self, context: RunContext
    ) -> ToolExecutionResult: ...
    def _store_tool_stage_outputs(
        self, context: RunContext, result: ToolExecutionResult
    ) -> None: ...
    def _build_paused_output(self, context: RunContext, result: ToolExecutionResult) -> Any: ...
    def _emit(self, event: EventSpec) -> None: ...
    def _save_checkpoint(self, context: RunContext, *, latest_output: Any, node_id: str) -> Any: ...
    def _maybe_fail_after_step(self, step_name: str) -> None: ...
    async def _maybe_execute_subagent_group(self, context: RunContext) -> None: ...


async def execute_tool_stage_step(host: ToolStageHost, context: RunContext) -> RuntimeStepResult:
    """Execute tool stage and route to interrupt, code-agent loop, or finalize."""
    _emit_tool_started_if_needed(host, context)
    result = await host._tool_result_with_approved_override(context)
    host._store_tool_stage_outputs(context, result)
    _post_process_tool_result(host, context, result)
    interrupt_result = _try_build_interrupt_transition(host, context, result)
    if interrupt_result is not None:
        return interrupt_result
    code_loop = _try_code_agent_loop_transition(host, context, result)
    if code_loop is not None:
        return code_loop
    return await _finalize_tool_stage_transition(host, context, result)


def _post_process_tool_result(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    planning_updated = apply_planning_updates_from_envelopes(context, result)
    observations = build_observations_from_tool_result(
        result,
        observation_max_chars=host._config.observation_max_chars,
    )
    if observations:
        context.metadata["observations"] = observations
    _update_tool_protocol_messages(context, result)
    _update_zero_result_policy(context, result)
    _refresh_force_final_controls(context)
    if not planning_updated:
        update_planning_state_from_tool_results(context)


def _try_build_interrupt_transition(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> RuntimeStepResult | None:
    if result.interrupt is None:
        return None
    pending = pending_interrupt_from_execution_result(result)
    if pending is None:
        raise RuntimeExecutionError(
            "interrupt result requires pending tool call envelope"
        )
    context.metadata["interrupt_payload"] = result.interrupt.model_dump(mode="json")
    context.metadata["pending_interrupt"] = serialize_pending_interrupt(pending)
    context.metadata["resume_target_step"] = "tool_stage"
    context.metadata.pop("approved_tool_call", None)
    context.metadata.update(
        {
            "next_step": "done",
            "step_count": context.step_count + 1,
            "tool_calls": context.tool_calls,
        }
    )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.INTERRUPT_REQUESTED,
        payload={"reason": result.interrupt.reason.value},
    )
    paused_output = host._build_paused_output(context, result)
    context.metadata["terminal_output"] = paused_output.model_dump(mode="json")
    host._save_checkpoint(context, latest_output=paused_output, node_id="tool_stage")
    return RuntimeStepResult(next_step="done")


def _try_code_agent_loop_transition(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> RuntimeStepResult | None:
    if context.run_input.agent_profile != AgentProfile.CODE_AGENT:
        return None
    if getattr(result, "has_final_answer", False):
        return None
    context.step_count += 1
    context.metadata.update(
        {
            "next_step": "llm_call",
            "step_count": context.step_count,
            "tool_calls": context.tool_calls,
            "resume_target_step": "llm_call",
        }
    )
    host._save_checkpoint(context, latest_output=None, node_id="tool_stage")
    _emit_tool_completed_if_needed(host, context, result)
    host._maybe_fail_after_step("tool_stage")
    return RuntimeStepResult(next_step="llm_call")


async def _finalize_tool_stage_transition(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> RuntimeStepResult:
    context.step_count += 1
    text_form_planned = False
    if context.llm_response is not None:
        for call in extract_planned_tool_calls(context.llm_response):
            metadata = call.metadata if isinstance(call.metadata, dict) else {}
            if metadata.get("text_form_source"):
                text_form_planned = True
                break
    continue_with_llm = bool(result.envelopes) and (
        context.llm_response is not None
        and (
            context.llm_response.finish_reason == LlmFinishReason.TOOL_CALLS
            or text_form_planned
        )
    )
    loop_iterations = int(context.metadata.get("tool_loop_iterations", 0))
    if continue_with_llm:
        loop_iterations += 1
        increment_tool_loops_since_todo_write(context)
    if continue_with_llm and context.run_input.agent_profile != AgentProfile.CODE_AGENT:
        _maybe_force_final_answer(context)
    context.metadata.update(
        {
            "next_step": "llm_call" if continue_with_llm else "finalize",
            "step_count": context.step_count,
            "tool_calls": context.tool_calls,
            "tool_loop_iterations": loop_iterations,
        }
    )
    host._save_checkpoint(context, latest_output=None, node_id="tool_stage")
    _emit_tool_completed_if_needed(host, context, result)
    await host._maybe_execute_subagent_group(context)
    host._maybe_fail_after_step("tool_stage")
    return RuntimeStepResult(next_step="llm_call" if continue_with_llm else "finalize")


def _emit_tool_completed_if_needed(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    if not result.traces:
        return
    planned_calls = extract_planned_tool_calls(context.llm_response) if context.llm_response else []
    args_by_call_id = {
        call.tool_call_id: call.args
        for call in planned_calls
        if isinstance(call.tool_call_id, str) and call.tool_call_id
    }
    fallback_args = [call.args for call in planned_calls]
    preview_paths_by_call_id: dict[str, list[str]] = {}
    fallback_preview_paths: list[list[str]] = []
    for envelope in result.envelopes:
        preview_paths: list[str] = []
        structured = envelope.structured_output
        if isinstance(structured, dict):
            if (
                envelope.call.tool_name == "glob_search"
                and isinstance(structured.get("results"), list)
            ):
                preview_paths = [
                    str(item)
                    for item in structured["results"]
                    if isinstance(item, str)
                ][:5]
            elif (
                envelope.call.tool_name == "web_search"
                and isinstance(structured.get("result_preview_urls"), list)
            ):
                preview_paths = [
                    str(item)
                    for item in structured["result_preview_urls"]
                    if isinstance(item, str)
                ][:5]
        fallback_preview_paths.append(preview_paths)
        if (
            isinstance(envelope.call.tool_call_id, str)
            and envelope.call.tool_call_id
            and preview_paths
        ):
            preview_paths_by_call_id[envelope.call.tool_call_id] = preview_paths
    tools = []
    for index, trace in enumerate(result.traces):
        row: dict[str, object] = {
            "tool_name": trace.tool_name,
            "tool_call_id": trace.tool_call_id,
            "args": args_by_call_id.get(trace.tool_call_id)
            if isinstance(trace.tool_call_id, str) and trace.tool_call_id
            else (fallback_args[index] if index < len(fallback_args) else {}),
            "status": trace.status.value,
            "result_summary": trace.result_summary,
            "error_code": trace.error_code,
            "truncated": trace.truncated,
            "result_preview_paths": (
                preview_paths_by_call_id.get(trace.tool_call_id, [])
                if isinstance(trace.tool_call_id, str) and trace.tool_call_id
                else (
                    fallback_preview_paths[index]
                    if index < len(fallback_preview_paths)
                    else []
                )
            ),
        }
        if index < len(result.envelopes):
            structured = result.envelopes[index].structured_output
            if isinstance(structured, dict):
                remediation = structured.get("remediation")
                if isinstance(remediation, str) and remediation.strip():
                    row["remediation"] = remediation.strip()
        tools.append(row)
    payload: dict[str, object] = {
        "tool_calls": len(result.traces),
        "statuses": [trace.status.value for trace in result.traces],
        "tools": tools,
    }
    snapshot = build_planning_snapshot(context)
    if snapshot is not None:
        payload["planning_snapshot"] = snapshot
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
        payload=payload,
    )


def _emit_tool_started_if_needed(host: ToolStageHost, context: RunContext) -> None:
    response = context.llm_response
    if response is None:
        return
    calls = extract_planned_tool_calls(response)
    if not calls:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.TOOL_CALL_STARTED,
        payload={
            "tool_calls": len(calls),
            "tools": [
                {
                    "tool_name": call.tool_name,
                    "tool_call_id": call.tool_call_id,
                    "args": call.args,
                }
                for call in calls
            ],
        },
    )


def _update_tool_protocol_messages(context: RunContext, result: ToolExecutionResult) -> None:
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls:
        return
    messages = _load_protocol_messages(context)
    assistant_tool_calls = [
        {
            "id": call.tool_call_id or f"call_{index}",
            "type": "function",
            "function": {
                "name": call.tool_name,
                "arguments": json.dumps(call.args, ensure_ascii=True),
            },
        }
        for index, call in enumerate(planned_calls)
    ]
    messages.append(
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content=strip_text_form_tool_calls(response.message.content or ""),
            metadata={"tool_calls": assistant_tool_calls},
        )
    )
    from agent_driver.llm.tool_result_unpacker import (
        extract_attachments_from_structured_output,
    )

    for envelope in result.envelopes:
        # Phase 13 H29.2 — split binary attachments (images, …) off the
        # structured payload BEFORE json-serialization so they don't
        # round-trip through string-coerced corruption. The attachments
        # ride on ChatMessage.metadata; the provider _payload() rebuilds
        # the native wire shape (e.g. OpenAI content list with image_url
        # blocks) when it sees them. Providers that don't natively
        # accept tool-role attachments still see the text content and
        # degrade gracefully.
        structured_for_protocol, attachments = (
            extract_attachments_from_structured_output(envelope.structured_output)
        )

        tool_payload: dict[str, Any] = {}
        if isinstance(structured_for_protocol, dict):
            tool_payload.update(
                _compact_tool_payload_for_protocol(
                    envelope.call.tool_name, structured_for_protocol
                )
            )
        tool_payload["truncated"] = bool(envelope.truncated)
        if envelope.summary and "summary" not in tool_payload:
            tool_payload["summary"] = envelope.summary
        if envelope.error is not None:
            tool_payload["error"] = envelope.error.model_dump(mode="json")
            tool_payload["error_code"] = envelope.error.code
        else:
            tool_payload["error_code"] = None
        content = (
            json.dumps(tool_payload, ensure_ascii=True)
            if tool_payload
            else (envelope.summary or "")
        )
        message_metadata: dict[str, Any] = {}
        if attachments:
            message_metadata["attachments"] = attachments
        messages.append(
            ChatMessage(
                role=ChatRole.TOOL,
                name=envelope.call.tool_name,
                tool_call_id=envelope.call.tool_call_id,
                content=content,
                metadata=message_metadata,
            )
        )
    _append_denial_recovery_message(context, result, messages)
    _append_python_policy_recovery_hint(context, result, messages)
    append_todo_progress_hint_after_substantive_tool(context, result, messages)
    _append_web_fetch_verification_hint(context, result, messages)
    _append_web_fetch_duplicate_guard(context, result, messages)
    if context.metadata.get("force_final_answer"):
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=force_final_answer_tool_message(),
            )
        )
    _normalize_protocol_messages(messages)
    context.metadata["protocol_messages"] = [
        item.model_dump(mode="json") for item in messages
    ]


def _compact_tool_payload_for_protocol(
    tool_name: str, structured: dict[str, Any]
) -> dict[str, Any]:
    """Shrink heavy tool payloads before they enter protocol_messages."""
    if tool_name != "web_fetch":
        return structured
    metadata = structured.get("metadata")
    compact: dict[str, Any] = {
        "summary": structured.get("summary"),
        "url": structured.get("url"),
        "status_code": structured.get("status_code"),
        "extract_mode": structured.get("extract_mode"),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "excerpt": structured.get("excerpt") or structured.get("content"),
        "truncated": structured.get("truncated"),
        "error_code": structured.get("error_code"),
    }
    if structured.get("error") is not None:
        compact["error"] = structured.get("error")
    excerpt = compact.get("excerpt")
    if isinstance(excerpt, str) and len(excerpt) > 2500:
        compact["excerpt"] = excerpt[:2500]
    return compact


def _append_python_policy_recovery_hint(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Nudge model after python import policy rejection (stdlib-only sandbox)."""
    if context.metadata.get("python_policy_hint_sent") is True:
        return
    for envelope in result.envelopes:
        if envelope.call.tool_name != "python":
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        if structured.get("error_kind") != "policy":
            continue
        allowed = structured.get("allowed_imports")
        if isinstance(allowed, list) and any(
            name in allowed for name in ("numpy", "scipy", "pandas")
        ):
            return
        allowed_text = (
            ", ".join(str(item) for item in allowed[:12])
            if isinstance(allowed, list) and allowed
            else "see Allowed imports in system policy"
        )
        remediation = structured.get("remediation")
        remediation_text = (
            str(remediation).strip()
            if isinstance(remediation, str) and remediation.strip()
            else f"Use allowed imports only: {allowed_text}"
        )
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "Python import was blocked by sandbox policy (not because scipy/numpy "
                    "are missing). Do not import numpy, scipy, pandas, or sklearn. "
                    f"{remediation_text}. For gamma/statistics use math and statistics."
                ),
            )
        )
        context.metadata["python_policy_hint_sent"] = True
        return


def _append_web_fetch_duplicate_guard(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Discourage repeated web_fetch when prior fetch already returned usable text."""
    saw_fetch = any(envelope.call.tool_name == "web_fetch" for envelope in result.envelopes)
    if not saw_fetch:
        return
    prior_fetch_total = int(context.metadata.get("web_fetch_calls_total", 0))
    if prior_fetch_total > 1:
        if context.metadata.get("web_fetch_duplicate_guard_sent") is True:
            return
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "You already fetched at least one URL. Do not call web_fetch again "
                    "unless the previous excerpt/metadata was clearly insufficient."
                ),
            )
        )
        context.metadata["web_fetch_duplicate_guard_sent"] = True


def _append_web_fetch_verification_hint(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Nudge model to verify web searches with at least one fetch."""
    web_search_total = int(context.metadata.get("web_search_calls_total", 0))
    web_fetch_total = int(context.metadata.get("web_fetch_calls_total", 0))
    for envelope in result.envelopes:
        if envelope.call.tool_name == "web_search":
            web_search_total += 1
        elif envelope.call.tool_name == "web_fetch":
            web_fetch_total += 1
    context.metadata["web_search_calls_total"] = web_search_total
    context.metadata["web_fetch_calls_total"] = web_fetch_total
    if web_search_total < 1 or web_fetch_total > 0:
        return
    if context.metadata.get("web_fetch_verification_hint_sent") is True:
        return
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                "You already used web_search. Before concluding on external-world facts, "
                "open at least one returned URL with web_fetch and cite that URL."
            ),
        )
    )
    context.metadata["web_fetch_verification_hint_sent"] = True


def _append_denial_recovery_message(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Append one-shot corrective hint after tool_handler_error denials."""
    denied_signature: str | None = None
    denied_tool_name: str | None = None
    denied_message: str | None = None
    denied_code: str | None = None
    for envelope in result.envelopes:
        error = envelope.error
        if error is None or error.code != "tool_handler_error":
            continue
        denied_tool_name = envelope.call.tool_name
        denied_code = error.code
        denied_message = (error.message or "").strip()
        denied_signature = f"{denied_tool_name}:{error.code}:{denied_message}"
        break
    if denied_signature is None:
        return
    if context.metadata.get("last_denied_signature") == denied_signature:
        return
    reason = denied_message or "tool handler policy denied this call"
    denied_counts = context.metadata.get("denied_tool_counts")
    if not isinstance(denied_counts, dict):
        denied_counts = {}
    tool_key = denied_tool_name or "unknown"
    prior_count = int(denied_counts.get(tool_key, 0))
    denied_counts[tool_key] = prior_count + 1
    context.metadata["denied_tool_counts"] = denied_counts
    if denied_counts[tool_key] >= 2:
        context.metadata["force_final_answer"] = True
        context.metadata["tool_choice_override"] = "none"
        context.metadata["force_final_answer_reason"] = "repeated_tool_handler_error"
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    f"Tool '{denied_tool_name}' failed twice with '{denied_code}'. "
                    "Stop calling this tool and answer with what you have, or ask one clarification."
                ),
            )
        )
        context.metadata["last_denied_signature"] = denied_signature
        return
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                f"Tool '{denied_tool_name}' was denied: {reason}. "
                "Retry with corrected arguments; do not repeat the same denied call."
            ),
        )
    )
    context.metadata["last_denied_signature"] = denied_signature


def _normalize_protocol_messages(messages: list[ChatMessage]) -> None:
    normalized: list[ChatMessage] = []
    total = len(messages)
    for index, message in enumerate(messages):
        if _is_drop_candidate_assistant_message(
            message,
            next_message=messages[index + 1] if index + 1 < total else None,
        ):
            continue
        if (
            normalized
            and normalized[-1].role == ChatRole.USER
            and message.role == ChatRole.USER
        ):
            merged = "\n\n".join(
                part
                for part in [
                    (normalized[-1].content or "").strip(),
                    (message.content or "").strip(),
                ]
                if part
            )
            normalized[-1] = ChatMessage(role=ChatRole.USER, content=merged)
            continue
        normalized.append(message)
    messages[:] = normalized


def _is_drop_candidate_assistant_message(
    message: ChatMessage, *, next_message: ChatMessage | None
) -> bool:
    if message.role != ChatRole.ASSISTANT:
        return False
    if (message.content or "").strip():
        return False
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    if metadata.get("tool_calls"):
        return False
    return next_message is not None and next_message.role == ChatRole.USER


def _load_protocol_messages(context: RunContext) -> list[ChatMessage]:
    payload = context.metadata.get("protocol_messages")
    messages: list[ChatMessage] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                messages.append(ChatMessage.model_validate(item))
    if messages:
        return messages
    if context.run_input.messages:
        return [message for message in context.run_input.messages]
    return [ChatMessage(role=ChatRole.USER, content=context.run_input.input or "")]


def _update_zero_result_policy(context: RunContext, result: ToolExecutionResult) -> None:
    zero_streak = int(context.metadata.get("web_search_zero_streak", 0))
    saw_web_search = False
    for envelope in result.envelopes:
        if envelope.call.tool_name != "web_search":
            continue
        saw_web_search = True
        rows = (
            envelope.structured_output.get("results")
            if isinstance(envelope.structured_output, dict)
            else None
        )
        parse_status = (
            str(envelope.structured_output.get("parse_status") or "")
            if isinstance(envelope.structured_output, dict)
            else ""
        )
        if parse_status == "upstream_error":
            continue
        if isinstance(rows, list) and rows:
            zero_streak = 0
        else:
            zero_streak += 1
    if not saw_web_search:
        return
    context.metadata["web_search_zero_streak"] = zero_streak
    if zero_streak >= 1:
        context.metadata["force_final_answer"] = True
        context.metadata["tool_choice_override"] = "none"
        context.metadata["force_final_answer_reason"] = "web_search_zero_results"


def _refresh_force_final_controls(context: RunContext) -> None:
    """Clear forced-final flags unless current run state still requires them."""
    if _should_force_final_answer(context):
        context.metadata["force_final_answer"] = True
        context.metadata["tool_choice_override"] = "none"
        return
    context.metadata.pop("force_final_answer", None)
    context.metadata.pop("tool_choice_override", None)
    context.metadata.pop("force_final_answer_reason", None)


def _maybe_force_final_answer(context: RunContext) -> None:
    """Enable forced final-answer mode only when guard heuristics trigger."""
    if _should_force_final_answer(context):
        context.metadata["force_final_answer"] = True
        context.metadata["tool_choice_override"] = "none"
        if "force_final_answer_reason" not in context.metadata:
            context.metadata["force_final_answer_reason"] = "runtime_guardrail"
        return
    context.metadata.pop("force_final_answer", None)
    context.metadata.pop("tool_choice_override", None)
    context.metadata.pop("force_final_answer_reason", None)


def _should_force_final_answer(context: RunContext) -> bool:
    """Return whether loop should force final answer on next LLM request."""
    max_tool_calls_raw = context.run_input.max_tool_calls
    max_steps_raw = context.run_input.max_steps
    max_tool_calls = (
        max(1, int(max_tool_calls_raw))
        if isinstance(max_tool_calls_raw, int)
        else max(1, int(context.metadata.get("max_tool_calls", 1)))
    )
    max_steps = (
        max(1, int(max_steps_raw))
        if isinstance(max_steps_raw, int)
        else max(1, int(context.metadata.get("max_steps", 1)))
    )
    near_tool_budget = context.tool_calls >= max(1, max_tool_calls - 1)
    near_step_budget = context.llm_step_count >= max(1, max_steps - 1)
    loop_detected = _has_repeated_recent_tool_call(context)
    zero_streak = int(context.metadata.get("web_search_zero_streak", 0))
    zero_results_triggered = zero_streak >= 1
    return near_tool_budget or near_step_budget or loop_detected or zero_results_triggered


def _has_repeated_recent_tool_call(context: RunContext) -> bool:
    """Detect two latest tool calls with identical tool name and args."""
    tool_results = context.metadata.get("tool_results")
    if not isinstance(tool_results, list):
        return False
    recent: list[tuple[str, str]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        if not tool_name:
            continue
        args = call.get("args")
        args_key = json.dumps(args, ensure_ascii=True, sort_keys=True)
        recent.append((tool_name, args_key))
    if len(recent) < 2:
        return False
    return recent[-1] == recent[-2]


__all__ = ["ToolStageHost", "execute_tool_stage_step"]
