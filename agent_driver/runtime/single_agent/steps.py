"""Async step handlers for SingleAgentRunner (LLM, tools, finalize)."""

from __future__ import annotations

from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.single_agent.llm import build_single_agent_llm_request
from agent_driver.runtime.single_agent.pending import (
    pending_interrupt_from_execution_result,
    serialize_pending_interrupt,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerDeps,
    RuntimeStepResult,
    TerminalResult,
)
from agent_driver.runtime.tools import ToolExecutionResult


class SingleAgentStepMixin:  # pylint: disable=too-few-public-methods
    """Mixin: deterministic step transitions after journal/output/resume."""

    _deps: RunnerDeps

    async def _tool_result_with_approved_override(
        self, context: RunContext
    ) -> ToolExecutionResult:
        """Execute tool stage, honoring approved-call override on resume."""
        if context.llm_response is None:
            raise RuntimeExecutionError("Missing LLM response before tool stage")
        approved_call = context.metadata.get("approved_tool_call")
        if isinstance(approved_call, dict):
            request = context.llm_response.model_copy(
                update={
                    "metadata": {
                        **context.llm_response.metadata,
                        "planned_tool_calls": [approved_call],
                    }
                }
            )
            return await self._deps.tool_executor(context.run_input, request)
        return await self._deps.tool_executor(context.run_input, context.llm_response)

    def _store_tool_stage_outputs(
        self, context: RunContext, result: ToolExecutionResult
    ) -> None:
        """Persist tool stage traces/results into context metadata."""
        context.tool_calls += len(result.traces)
        context.metadata["tool_trace"] = [
            trace.model_dump(mode="json") for trace in result.traces
        ]
        context.metadata["tool_results"] = [
            item.model_dump(mode="json") for item in result.envelopes
        ]

    async def _execute_run_started(self, context: RunContext) -> RuntimeStepResult:
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_STARTED,
                payload={"agent_id": context.run_input.agent_id},
            )
        )
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "llm_call",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            }
        )
        self._save_checkpoint(context, latest_output=None, node_id="run_started")
        self._maybe_fail_after_step("run_started")
        return RuntimeStepResult(next_step="llm_call")

    async def _execute_llm_call(self, context: RunContext) -> RuntimeStepResult:
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.LLM_CALL_STARTED,
                payload={"provider": self._deps.provider.name},
            )
        )
        clarification = context.metadata.get("clarification")
        try:
            context.llm_response = await self._deps.provider.complete(
                build_single_agent_llm_request(
                    run_input=context.run_input,
                    clarification=(
                        clarification if isinstance(clarification, str) else None
                    ),
                )
            )
        except (RuntimeError, ValueError) as exc:
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_FAILED,
                    payload={"reason": TerminalReason.MODEL_ERROR.value},
                )
            )
            raise RuntimeExecutionError("LLM completion failed") from exc
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.LLM_CALL_COMPLETED,
                payload={
                    "provider": context.llm_response.provider,
                    "model": context.llm_response.model,
                    "finish_reason": context.llm_response.finish_reason.value,
                },
            )
        )
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "tool_stage",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
                "last_llm_response": context.llm_response.model_dump(mode="json"),
            }
        )
        self._save_checkpoint(context, latest_output=None, node_id="llm_call")
        self._maybe_fail_after_step("llm_call")
        return RuntimeStepResult(next_step="tool_stage")

    async def _execute_tool_stage(self, context: RunContext) -> RuntimeStepResult:
        result = await self._tool_result_with_approved_override(context)
        self._store_tool_stage_outputs(context, result)
        if result.interrupt is not None:
            pending = pending_interrupt_from_execution_result(result)
            if pending is None:
                raise RuntimeExecutionError(
                    "interrupt result requires pending tool call envelope"
                )
            context.metadata["interrupt_payload"] = result.interrupt.model_dump(
                mode="json"
            )
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
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.INTERRUPT_REQUESTED,
                    payload={"reason": result.interrupt.reason.value},
                )
            )
            paused_output = self._build_paused_output(context, result)
            context.metadata["terminal_output"] = paused_output.model_dump(mode="json")
            self._save_checkpoint(
                context, latest_output=paused_output, node_id="tool_stage"
            )
            return RuntimeStepResult(next_step="done")
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "finalize",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            }
        )
        self._save_checkpoint(context, latest_output=None, node_id="tool_stage")
        if result.traces:
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
                    payload={
                        "tool_calls": len(result.traces),
                        "statuses": [trace.status.value for trace in result.traces],
                    },
                )
            )
        self._maybe_fail_after_step("tool_stage")
        return RuntimeStepResult(next_step="finalize")

    async def _execute_finalize(self, context: RunContext) -> RuntimeStepResult:
        if context.llm_response is None and isinstance(
            context.metadata.get("last_llm_response"), dict
        ):
            context.llm_response = LlmResponse.model_validate(
                context.metadata["last_llm_response"]
            )
        finish_reason = (
            context.llm_response.finish_reason.value
            if context.llm_response
            else "unknown"
        )
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_COMPLETED,
                payload={"finish_reason": finish_reason},
            )
        )
        output = self._build_output(
            context,
            TerminalResult(
                status=RunStatus.COMPLETED,
                reason=TerminalReason.FINAL_ANSWER,
            ),
        )
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "done",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            }
        )
        output.checkpoint = self._save_checkpoint(
            context,
            latest_output=output,
            node_id="finalize",
        )
        self._maybe_fail_after_step("finalize")
        context.metadata["terminal_output"] = output.model_dump(mode="json")
        return RuntimeStepResult(next_step="done")

    async def _execute_step(self, context: RunContext) -> RuntimeStepResult:
        if context.step_name == "run_started":
            return await self._execute_run_started(context)
        if context.step_name == "llm_call":
            return await self._execute_llm_call(context)
        if context.step_name == "tool_stage":
            return await self._execute_tool_stage(context)
        if context.step_name == "finalize":
            return await self._execute_finalize(context)
        raise RuntimeExecutionError(f"Unknown step '{context.step_name}'")


__all__ = ["SingleAgentStepMixin"]
