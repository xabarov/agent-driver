"""Async step handlers for SingleAgentRunner (LLM, tools, finalize)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
    CompactionOrchestrator,
    build_observation_memory,
    build_session_memory_compaction,
    evaluate_session_memory_freshness,
    load_session_memory,
    microcompact_observations,
    planning_state_init,
    planning_state_set_step,
    render_planning_step_prompt,
    run_full_llm_compaction,
    sanitize_compaction_text,
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
from agent_driver.contracts.messages import ChatMessage
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
from agent_driver.tools import apply_planning_state_tool_update


class SingleAgentStepMixin:  # pylint: disable=too-few-public-methods
    """Mixin: deterministic step transitions after journal/output/resume."""

    _deps: RunnerDeps
    _config: RunnerConfig
    _compaction_orchestrator: CompactionOrchestrator | None = None

    def _get_compaction_orchestrator(self) -> CompactionOrchestrator:
        """Lazily initialize compaction orchestrator."""
        if self._compaction_orchestrator is None:
            self._compaction_orchestrator = CompactionOrchestrator(
                failure_limit=self._config.compaction_failure_limit
            )
        return self._compaction_orchestrator

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
            structured = envelope.structured_output
            if isinstance(structured, dict):
                raw_observations = structured.get("observations")
                if isinstance(raw_observations, list):
                    for row in raw_observations:
                        if not isinstance(row, dict):
                            continue
                        preview = row.get("text_preview")
                        source_raw = row.get("source")
                        if not isinstance(preview, str):
                            continue
                        source_map = {
                            "stdout": ObservationSource.TOOL_STDOUT,
                            "stderr": ObservationSource.TOOL_STDERR,
                        }
                        source = source_map.get(
                            str(source_raw).lower(), ObservationSource.TOOL_LOG
                        )
                        extra_observation = build_observation_memory(
                            text=preview,
                            source=source,
                            trust=ObservationTrust.UNVERIFIED,
                            max_chars=self._config.observation_max_chars,
                            tool_name=envelope.call.tool_name,
                            tool_call_id=envelope.call.tool_call_id,
                        )
                        observations.append(extra_observation.model_dump(mode="json"))
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
            micro = microcompact_observations(
                [item for item in observations if isinstance(item, dict)],
                preserve_recent=self._config.microcompact_preserve_recent,
                max_preview_chars=self._config.microcompact_max_preview_chars,
            )
            observations = micro.observations
            context.metadata["observations"] = observations
            context.metadata["microcompaction_audit"] = micro.audit
            context.metadata["microcompaction"] = {
                "bytes_saved": micro.bytes_saved,
                "estimated_tokens_saved": micro.estimated_tokens_saved,
            }
            digest_refs = context.metadata.get("digest_refs", [])
            if not isinstance(digest_refs, list):
                digest_refs = []
            artifact_refs = context.metadata.get("artifact_refs", [])
            if not isinstance(artifact_refs, list):
                artifact_refs = []
            planning_prompt = None
            planning_step_payload = context.metadata.get("planning_step")
            if self._config.include_planning_prompt and isinstance(
                planning_step_payload, dict
            ):
                planning_prompt = render_planning_step_prompt(
                    PlanningStep.model_validate(planning_step_payload)
                )
            request, trim_payload = build_single_agent_llm_request(
                run_input=context.run_input,
                clarification=(
                    clarification if isinstance(clarification, str) else None
                ),
                tool_docs=(
                    context.metadata["code_tool_docs"]
                    if isinstance(context.metadata.get("code_tool_docs"), str)
                    else None
                ),
                authorized_imports=self._config.authorized_imports,
                registry=self._deps.tool_registry,
                observations=[item for item in observations if isinstance(item, dict)],
                planning_prompt=planning_prompt,
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
                max_observations=self._config.trim_max_observations,
                context_window_estimate=self._config.context_window_estimate,
                warning_threshold=self._config.token_warning_threshold,
                compact_threshold=self._config.token_compact_threshold,
                blocking_threshold=self._config.token_blocking_threshold,
                output_token_reserve=self._config.output_token_reserve,
            )
            context.metadata["trim_audit"] = trim_payload["trim_audit"]
            context.metadata["trim_metadata"] = trim_payload["trim_metadata"]
            context.metadata["token_pressure"] = trim_payload["token_pressure"]
            context.metadata["prompt_render"] = trim_payload["prompt_render"]
            token_pressure = context.metadata.get("token_pressure", {})
            token_state = "ok"
            if isinstance(token_pressure, dict):
                token_state = str(token_pressure.get("state", "ok"))
            await self._apply_compaction_if_eligible(
                context=context,
                request=request,
                token_pressure_state=token_state,
            )
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
        token_pressure = context.metadata.get("token_pressure", {})
        if isinstance(token_pressure, dict):
            state = str(token_pressure.get("state", "ok"))
            if state in {"warning", "compact_recommended", "blocking"}:
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.WARNING,
                        payload={
                            "kind": "token_pressure",
                            "state": state,
                            "used_tokens_estimate": token_pressure.get(
                                "used_tokens_estimate"
                            ),
                            "blocking_threshold": token_pressure.get(
                                "blocking_threshold"
                            ),
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

    async def _apply_compaction_if_eligible(
        self,
        *,
        context: RunContext,
        request: Any,
        token_pressure_state: str,
    ) -> None:
        """Run compaction orchestration before final provider completion."""
        orchestrator = self._get_compaction_orchestrator()
        session_memory = load_session_memory(
            artifact_store=self._deps.artifact_store,
            session_id=context.run_input.thread_id or context.run_id,
        )
        decision = orchestrator.decide(
            enable_compaction=self._config.enable_compaction,
            enable_session_memory_compaction=self._config.enable_session_memory_compaction,
            enable_llm_compaction=self._config.enable_llm_compaction,
            token_pressure_state=token_pressure_state,
            session_memory=session_memory,
        )
        context.metadata[COMPACTION_DECISION_KEY] = decision.model_dump(mode="json")
        if not decision.eligible:
            context.metadata[COMPACTION_AUDIT_KEY] = {"decision": context.metadata[COMPACTION_DECISION_KEY]}
            return
        if decision.mode.value == "session_memory" and session_memory is not None:
            freshness = evaluate_session_memory_freshness(
                session_memory=session_memory,
                latest_turn_index=int(context.metadata.get("step_count", 0)),
                stale_after_turns=self._config.session_memory_stale_after_turns,
            )
            if freshness.state == "fresh":
                compacted = build_session_memory_compaction(
                    session_memory=session_memory,
                    recent_tail_messages=[msg.model_dump(mode="json") for msg in request.messages],
                    planning_state=(
                        context.metadata.get("planning_state")
                        if isinstance(context.metadata.get("planning_state"), dict)
                        else None
                    ),
                    retained_digest_ids=[
                        str(item.get("digest_id"))
                        for item in context.metadata.get("digest_refs", [])
                        if isinstance(item, dict) and item.get("digest_id")
                    ],
                    retained_artifact_ids=[
                        str(item.get("artifact_id"))
                        for item in context.metadata.get("artifact_refs", [])
                        if isinstance(item, dict) and item.get("artifact_id")
                    ],
                )
                request.messages = [
                    ChatMessage.model_validate(item) for item in compacted.prompt_messages
                ]
                result_payload = {
                    "compaction_id": "cmp_session_memory",
                    "mode": "session_memory",
                    "success": True,
                    "retained_digest_ids": compacted.retained_digest_ids,
                    "retained_artifact_ids": compacted.retained_artifact_ids,
                    "metadata": {"freshness": freshness.state, "reason": freshness.reason},
                }
                context.metadata[COMPACTION_RESULT_KEY] = result_payload
                context.metadata["retained_digest_ids"] = compacted.retained_digest_ids
                context.metadata["retained_artifact_ids"] = compacted.retained_artifact_ids
                context.metadata[COMPACTION_AUDIT_KEY] = {
                    "decision": context.metadata[COMPACTION_DECISION_KEY],
                    "result": result_payload,
                }
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.MEMORY_COMPACTED,
                        payload={
                            "mode": "session_memory",
                            "retained_digest_ids": compacted.retained_digest_ids,
                            "retained_artifact_ids": compacted.retained_artifact_ids,
                        },
                    )
                )
                orchestrator.reset_failures()
                return
        if decision.mode.value == "llm_full":
            history_excerpt = "\n".join(message.content for message in request.messages[-8:])
            sanitized_excerpt = sanitize_compaction_text(history_excerpt)
            compaction_result, summary = await run_full_llm_compaction(
                provider=self._deps.provider,
                model=self._config.compaction_model,
                history_excerpt=sanitized_excerpt,
                user_request=context.run_input.input or "",
            )
            if compaction_result is not None and compaction_result.success:
                request.messages = request.messages[-4:]
                summary_text = str(summary.get("current_work", ""))
                request.messages.append(
                    ChatMessage.model_validate(
                        {"role": "system", "content": f"Compacted summary:\n{summary_text}"}
                    )
                )
                context.metadata[COMPACTION_RESULT_KEY] = compaction_result.model_dump(
                    mode="json"
                )
                context.metadata[COMPACTION_AUDIT_KEY] = {
                    "decision": context.metadata[COMPACTION_DECISION_KEY],
                    "result": context.metadata[COMPACTION_RESULT_KEY],
                }
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.MEMORY_COMPACTED,
                        payload={
                            "mode": "llm_full",
                            "model": compaction_result.model,
                            "latency_ms": compaction_result.latency_ms,
                            "input_tokens_estimate": compaction_result.input_tokens_estimate,
                            "output_tokens_estimate": compaction_result.output_tokens_estimate,
                        },
                    )
                )
                orchestrator.reset_failures()
                return
        placeholder = orchestrator.execute_placeholder(decision)
        context.metadata[COMPACTION_AUDIT_KEY] = placeholder.model_dump(mode="json")
        context.metadata[COMPACTION_RESULT_KEY] = (
            placeholder.result.model_dump(mode="json") if placeholder.result else None
        )
        context.metadata[COMPACTION_FAILURES_KEY] = placeholder.failures

    async def _execute_tool_stage(  # pylint: disable=too-many-branches
        self, context: RunContext
    ) -> RuntimeStepResult:
        result = await self._tool_result_with_approved_override(context)
        self._store_tool_stage_outputs(context, result)
        planning_state_payload = context.metadata.get("planning_state")
        if isinstance(planning_state_payload, dict):
            planning_state = PlanningState.model_validate(planning_state_payload)
        else:
            planning_state = planning_state_init(context.run_id)
        planning_updated_by_tool = False
        for envelope in result.envelopes:
            if envelope.call.tool_name != "planning_state_update":
                continue
            structured = envelope.structured_output
            if not isinstance(structured, dict):
                continue
            planning_updated_by_tool = True
            planning_state = apply_planning_state_tool_update(
                planning_state, structured.get("applied_args", {})
            )
            if isinstance(structured.get("planning_step"), dict):
                context.metadata["planning_step"] = structured["planning_step"]
        context.metadata["planning_state"] = planning_state.model_dump(mode="json")
        observations = self._build_observations(result)
        if observations:
            context.metadata["observations"] = observations
        if not planning_updated_by_tool:
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
        if context.run_input.agent_profile == AgentProfile.CODE_AGENT and not getattr(
            result, "has_final_answer", False
        ):
            context.step_count += 1
            context.metadata.update(
                {
                    "next_step": "llm_call",
                    "step_count": context.step_count,
                    "tool_calls": context.tool_calls,
                    "resume_target_step": "llm_call",
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
            return RuntimeStepResult(next_step="llm_call")
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
