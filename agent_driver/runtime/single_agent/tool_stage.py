"""Tool stage execution and transitions."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.contracts.enums import AgentProfile, RuntimeEventType
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
    context.metadata.update(
        {
            "next_step": "finalize",
            "step_count": context.step_count,
            "tool_calls": context.tool_calls,
        }
    )
    host._save_checkpoint(context, latest_output=None, node_id="tool_stage")
    _emit_tool_completed_if_needed(host, context, result)
    await host._maybe_execute_subagent_group(context)
    host._maybe_fail_after_step("tool_stage")
    return RuntimeStepResult(next_step="finalize")


def _emit_tool_completed_if_needed(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    if not result.traces:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
        payload={
            "tool_calls": len(result.traces),
            "statuses": [trace.status.value for trace in result.traces],
        },
    )


__all__ = ["ToolStageHost", "execute_tool_stage_step"]
