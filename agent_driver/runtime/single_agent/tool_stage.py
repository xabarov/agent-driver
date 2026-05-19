"""Tool stage execution and transitions."""

from __future__ import annotations

import json
from typing import Any, Protocol

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.contracts.enums import AgentProfile, ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason
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
    update_planning_state_from_tool_results,
)
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
    continue_with_llm = bool(result.envelopes) and (
        context.llm_response is not None
        and context.llm_response.finish_reason == LlmFinishReason.TOOL_CALLS
    )
    loop_iterations = int(context.metadata.get("tool_loop_iterations", 0))
    if continue_with_llm:
        loop_iterations += 1
    if continue_with_llm and context.run_input.agent_profile != AgentProfile.CODE_AGENT:
        context.metadata["tool_choice_override"] = "none"
        context.metadata["force_final_answer"] = True
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
    tools = [
        {
            "tool_name": trace.tool_name,
            "tool_call_id": trace.tool_call_id,
            "status": trace.status.value,
            "result_summary": trace.result_summary,
            "error_code": trace.error_code,
            "truncated": trace.truncated,
        }
        for trace in result.traces
    ]
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
        payload={
            "tool_calls": len(result.traces),
            "statuses": [trace.status.value for trace in result.traces],
            "tools": tools,
        },
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
            content=response.message.content or "",
            metadata={"tool_calls": assistant_tool_calls},
        )
    )
    for envelope in result.envelopes:
        tool_payload: dict[str, Any] = {}
        if isinstance(envelope.structured_output, dict):
            tool_payload.update(envelope.structured_output)
        if envelope.summary and "summary" not in tool_payload:
            tool_payload["summary"] = envelope.summary
        if envelope.error is not None:
            tool_payload["error"] = envelope.error.model_dump(mode="json")
        content = (
            json.dumps(tool_payload, ensure_ascii=True)
            if tool_payload
            else (envelope.summary or "")
        )
        messages.append(
            ChatMessage(
                role=ChatRole.TOOL,
                name=envelope.call.tool_name,
                tool_call_id=envelope.call.tool_call_id,
                content=content,
            )
        )
    if context.metadata.get("force_final_answer"):
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "Answer the user using tool results. "
                    "Do not call additional tools unless absolutely required."
                ),
            )
        )
    context.metadata["protocol_messages"] = [
        item.model_dump(mode="json") for item in messages
    ]


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
        if isinstance(rows, list) and rows:
            zero_streak = 0
        else:
            zero_streak += 1
    if not saw_web_search:
        return
    context.metadata["web_search_zero_streak"] = zero_streak
    if zero_streak >= 2:
        context.metadata["force_final_answer"] = True
        context.metadata["tool_choice_override"] = "none"
        context.metadata["force_final_answer_reason"] = "web_search_zero_results"


__all__ = ["ToolStageHost", "execute_tool_stage_step"]
