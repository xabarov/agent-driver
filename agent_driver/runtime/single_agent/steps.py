"""Async step handlers for SingleAgentRunner (LLM, tools, finalize)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.context import (
    build_observation_memory,
    planning_state_init,
    planning_state_set_step,
)
from agent_driver.contracts.context import PlanningState, PlanningStep
from agent_driver.contracts.enums import (
    AgentProfile,
    ObservationSource,
    ObservationTrust,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
)
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
    RunnerConfig,
    RunnerDeps,
    RuntimeStepResult,
    TerminalResult,
)
from agent_driver.runtime.tools import ToolExecutionResult


class SingleAgentStepMixin:  # pylint: disable=too-few-public-methods
    """Mixin: deterministic step transitions after journal/output/resume."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _build_observations(self, result: ToolExecutionResult) -> list[dict[str, Any]]:
        """Build bounded observation rows from tool envelopes."""
        observations: list[dict[str, Any]] = []
        for envelope in result.envelopes:
            if envelope.summary is None:
                continue
            observation = build_observation_memory(
                text=envelope.summary,
                source=ObservationSource.TOOL_LOG,
                trust=ObservationTrust.UNVERIFIED,
                max_chars=self._config.observation_max_chars,
                tool_name=envelope.call.tool_name,
                tool_call_id=envelope.call.tool_call_id,
            )
            observations.append(observation.model_dump(mode="json"))
        return observations

    def _update_planning_state(self, context: RunContext) -> None:
        """Update minimal planning state and latest planning step payload."""
        tool_results = context.metadata.get("tool_results", [])
        if not isinstance(tool_results, list):
            tool_results = []
        facts_learned = [
            str(item.get("summary", ""))
            for item in tool_results
            if isinstance(item, dict) and isinstance(item.get("summary"), str)
        ]
        planning_step = PlanningStep(
            step_id=f"plan_{uuid4().hex[:8]}",
            facts_given=[context.run_input.input or ""],
            facts_learned=facts_learned[:3],
            facts_to_lookup=[],
            facts_to_derive=[],
            next_plan="Continue execution",
            metadata={"run_id": context.run_id},
        )
        planning_state_payload = context.metadata.get("planning_state")
        if isinstance(planning_state_payload, dict):
            state = planning_state_set_step(
                PlanningState.model_validate(planning_state_payload), planning_step
            )
        else:
            state = planning_state_set_step(
                planning_state_init(context.run_id), planning_step
            )
        context.metadata["planning_step"] = planning_step.model_dump(mode="json")
        context.metadata["planning_state"] = state.model_dump(mode="json")

    async def _tool_result_with_approved_override(
        self, context: RunContext
    ) -> ToolExecutionResult:
        """Execute tool stage, honoring approved-call override on resume."""
        if context.run_input.agent_profile == AgentProfile.CODE_AGENT:
            return await run_code_agent_stage(runner=self, context=context)
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
            observations = context.metadata.get("observations", [])
            if not isinstance(observations, list):
                observations = []
            digest_refs = context.metadata.get("digest_refs", [])
            if not isinstance(digest_refs, list):
                digest_refs = []
            artifact_refs = context.metadata.get("artifact_refs", [])
            if not isinstance(artifact_refs, list):
                artifact_refs = []
            request, trim_payload = build_single_agent_llm_request(
                run_input=context.run_input,
                clarification=(
                    clarification if isinstance(clarification, str) else None
                ),
                observations=[item for item in observations if isinstance(item, dict)],
                digest_ids=[
                    str(item.get("digest_id"))
                    for item in digest_refs
                    if isinstance(item, dict) and item.get("digest_id")
                ],
                artifact_ids=[
                    str(item.get("artifact_id"))
                    for item in artifact_refs
                    if isinstance(item, dict) and item.get("artifact_id")
                ],
                max_chars=self._config.trim_max_chars,
                max_messages=self._config.trim_max_messages,
            )
            context.metadata["trim_audit"] = trim_payload["trim_audit"]
            context.metadata["trim_metadata"] = trim_payload["trim_metadata"]
            context.llm_response = await self._deps.provider.complete(request)
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
        observations = self._build_observations(result)
        if observations:
            context.metadata["observations"] = observations
        self._update_planning_state(context)
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
