"""Async step handlers for SingleAgentRunner (LLM, tools, finalize)."""

from __future__ import annotations

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.context import CompactionOrchestrator
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.control.dispatcher import drain_step_boundary_controls
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.lifecycle_hooks import dispatch_finalize, dispatch_run_start
from agent_driver.runtime.metadata_state import (
    get_loop_control_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_artifacts import (
    ensure_deep_research_report_artifact_metadata,
)
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.llm_step import execute_llm_call_step
from agent_driver.runtime.single_agent.planning.state import build_planning_snapshot
from agent_driver.runtime.single_agent.research.gating import (
    _build_continuation_transition,
    _maybe_build_continuation_transition,
    _tool_gate_for_context,
)

# Hard backstop on goal-gate (rubric) revision loops, independent of any
# hook's own iteration budget — prevents an always-revising hook from looping.
_MAX_RUBRIC_REVISIONS = 10
from agent_driver.runtime.single_agent.tool_stage import execute_tool_stage_step
from agent_driver.runtime.single_agent.tool_stage.subagent_execution import (
    maybe_execute_subagent_group,
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


class SingleAgentStepMixin:
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

    async def _apply_compaction_if_eligible(
        self,
        *,
        context: RunContext,
        request: object,
        token_pressure_state: str,
    ) -> None:
        await apply_compaction_if_eligible(
            self,
            context=context,
            request=request,
            token_pressure_state=token_pressure_state,
        )

    async def _tool_result_with_approved_override(
        self, context: RunContext
    ) -> ToolExecutionResult:
        """Execute tool stage, honoring approved-call override on resume."""
        from agent_driver.contracts.enums import AgentProfile

        if context.run_input.agent_profile == AgentProfile.CODE_AGENT:
            return await run_code_agent_stage(runner=self, context=context)
        if context.llm_response is None:
            raise RuntimeExecutionError("Missing LLM response before tool stage")
        approved_call = get_tool_loop_state(context).pop_approved_tool_call()
        # A0.2 — only forward ``tool_gate`` when the caller actually set
        # one. Old executors and test mocks have ``(run_input,
        # llm_response)`` signatures and would reject an unknown kwarg;
        # the new contract documented in ``runtime/tools.py`` allows
        # ``tool_gate`` but we don't force it on the wire when None.
        tool_gate = _tool_gate_for_context(context)
        gate_kwargs = {"tool_gate": tool_gate} if tool_gate is not None else {}
        if isinstance(approved_call, dict):
            request = context.llm_response.model_copy(
                update={
                    "metadata": {
                        **context.llm_response.metadata,
                        "planned_tool_calls": [approved_call],
                    }
                }
            )
            return await self._deps.tool_executor(
                context.run_input, request, **gate_kwargs
            )
        return await self._deps.tool_executor(
            context.run_input, context.llm_response, **gate_kwargs
        )

    def _store_tool_stage_outputs(
        self, context: RunContext, result: ToolExecutionResult
    ) -> None:
        """Persist tool stage traces/results into context metadata."""
        context.tool_calls += len(result.traces)
        get_tool_loop_state(context).append_stage_outputs(
            traces=[trace.model_dump(mode="json") for trace in result.traces],
            results=[item.model_dump(mode="json") for item in result.envelopes],
        )

    def _apply_node_contract_run_start(self, context: RunContext) -> None:
        """Layer A + prelude prep: validate allowed_tools and stage the prelude.

        Opt-in and inert unless ``AgentRunInput.node_contract`` is active. Diffs the
        declared tool names against the live registry (Layer A) and prepares the
        Layer-B proactive prelude from the tools that will actually surface, so the
        :class:`NodeContractLifecycleHook` can weave it into the system prompt.
        """
        from agent_driver.runtime.single_agent import node_contract as nc
        from agent_driver.runtime.single_agent.llm_step.build import (
            effective_tool_names_from_registry,
        )

        run_input = context.run_input
        if not nc.is_active(run_input):
            return
        registry = self._deps.tool_registry
        registered = (
            tuple(registry.list_names())
            if registry is not None and hasattr(registry, "list_names")
            else ()
        )
        unsatisfiable = nc.unsatisfiable_tool_names(run_input, registered)
        if unsatisfiable:
            context.metadata[nc.TOOL_POLICY_WARNINGS_KEY] = unsatisfiable
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.NODE_CONTRACT_WARNING,
                    payload={
                        "kind": "tool_policy_unsatisfiable",
                        "tools": list(unsatisfiable),
                        "detail": (
                            "declared allowed_tools / finalize_when_tools are not "
                            "callable in the registry"
                        ),
                    },
                )
            )
        policy = run_input.tool_policy
        surfaced = effective_tool_names_from_registry(
            registry,
            allowed=tuple(policy.allowed_tools) if policy.allowed_tools else None,
            denied=tuple(policy.denied_tools) if policy.denied_tools else None,
        )
        prelude = nc.build_prelude(run_input, surfaced)
        if prelude:
            context.metadata[nc.NODE_CONTRACT_PRELUDE_KEY] = prelude

    async def _execute_run_started(self, context: RunContext) -> RuntimeStepResult:
        from agent_driver.runtime.single_agent.planning.state import (
            apply_planning_state_seed_from_metadata,
        )

        apply_planning_state_seed_from_metadata(context)
        self._apply_node_contract_run_start(context)
        await dispatch_run_start(self._deps.lifecycle_hooks, context)
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_STARTED,
                payload={"agent_id": context.run_input.agent_id},
            )
        )
        context.step_count += 1
        get_loop_control_state(context).set_step_transition(
            next_step="llm_call",
            tool_calls=context.tool_calls,
        )
        self._save_checkpoint(context, latest_output=None, node_id="run_started")
        self._maybe_fail_after_step("run_started")
        return RuntimeStepResult(next_step="llm_call")

    async def _execute_llm_call(self, context: RunContext) -> RuntimeStepResult:
        applied_controls = drain_step_boundary_controls(
            context=context,
            store=self._deps.command_queue_store,
        )
        for item in applied_controls:
            payload = {
                "queue_id": item.queue_id,
                "control_id": item.control_id,
                "kind": item.kind.value,
                "priority": item.priority.value,
            }
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.COMMAND_DEQUEUED,
                    payload=payload,
                )
            )
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.CONTROL_APPLIED,
                    payload=payload,
                )
            )
        return await execute_llm_call_step(self, context)

    async def _execute_tool_stage(self, context: RunContext) -> RuntimeStepResult:
        return await execute_tool_stage_step(self, context)

    async def _maybe_execute_subagent_group(self, context: RunContext) -> None:
        await maybe_execute_subagent_group(self, context)

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
        completed_payload: dict[str, object] = {"finish_reason": finish_reason}
        force_final_reason = get_tool_loop_state(context).force_final_answer_reason()
        if isinstance(force_final_reason, str) and force_final_reason:
            completed_payload["force_final_reason"] = force_final_reason
        continuation_reason = context.metadata.get("continuation_nudge_reason")
        if isinstance(continuation_reason, str) and continuation_reason:
            completed_payload["continuation_reason"] = continuation_reason
        research_artifacts = ensure_deep_research_report_artifact_metadata(context)
        if isinstance(research_artifacts, dict):
            completed_payload["deep_research_artifacts"] = dict(research_artifacts)
        if context.llm_response is not None and context.llm_response.usage is not None:
            completed_payload["usage"] = context.llm_response.usage.model_dump(
                mode="json"
            )
        snapshot = build_planning_snapshot(context)
        if snapshot is not None:
            completed_payload["planning_snapshot"] = snapshot
        continuation = _maybe_build_continuation_transition(context)
        if continuation is not None:
            context.step_count += 1
            get_loop_control_state(context).set_step_transition(
                next_step="llm_call",
                tool_calls=context.tool_calls,
            )
            self._save_checkpoint(context, latest_output=None, node_id="finalize")
            self._maybe_fail_after_step("finalize")
            return continuation
        node_contract_reprompt = self._maybe_node_contract_tool_use_reprompt(context)
        if node_contract_reprompt is not None:
            context.step_count += 1
            get_loop_control_state(context).set_step_transition(
                next_step="llm_call",
                tool_calls=context.tool_calls,
            )
            self._save_checkpoint(context, latest_output=None, node_id="finalize")
            self._maybe_fail_after_step("finalize")
            return node_contract_reprompt
        terminal_answer = self._sanitize_terminal_answer(context)
        if terminal_answer:
            completed_payload["answer"] = terminal_answer
        revision = await dispatch_finalize(
            self._deps.lifecycle_hooks, context, answer=terminal_answer or ""
        )
        if revision is not None and (
            int(context.metadata.get("rubric_revision_count", 0))
            < _MAX_RUBRIC_REVISIONS
        ):
            # A goal-gate (rubric) hook is not satisfied: inject its feedback as
            # a user turn and resume instead of finishing.
            revise = _build_continuation_transition(
                context,
                text=terminal_answer or "",
                nudge=revision.feedback,
                reason="rubric_revision",
                count_key="rubric_revision_count",
            )
            context.step_count += 1
            get_loop_control_state(context).set_step_transition(
                next_step="llm_call",
                tool_calls=context.tool_calls,
            )
            self._save_checkpoint(context, latest_output=None, node_id="finalize")
            self._maybe_fail_after_step("finalize")
            return revise
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_COMPLETED,
                payload=completed_payload,
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
        get_loop_control_state(context).set_step_transition(
            next_step="done",
            tool_calls=context.tool_calls,
        )
        output.checkpoint = self._save_checkpoint(
            context,
            latest_output=output,
            node_id="finalize",
        )
        self._maybe_fail_after_step("finalize")
        get_loop_control_state(context).set_terminal_output(
            output.model_dump(mode="json")
        )
        return RuntimeStepResult(next_step="done")

    def _maybe_node_contract_tool_use_reprompt(
        self, context: RunContext
    ) -> RuntimeStepResult | None:
        """Layer B reactive guard: reprompt a zero-tool-call finalize, then escalate.

        Returns a continuation transition (back to ``llm_call``) while the reprompt
        budget remains, ``None`` otherwise. On the final attempt it stamps a typed
        ``no_tool_use`` violation so the run finalizes with a structured error rather
        than a silent generic answer.
        """
        from agent_driver.runtime.single_agent import node_contract as nc

        if not nc.tool_use_violation_pending(context):
            return None
        if not nc.reprompt_budget_remaining(context):
            nc.stamp_no_tool_use_violation(context)
            return None
        text = (
            context.llm_response.message.content
            if context.llm_response is not None
            else ""
        )
        return _build_continuation_transition(
            context,
            text=text or "",
            nudge=nc.build_tool_use_reprompt(context.run_input),
            reason=nc._REPROMPT_REASON,
            count_key=nc.TOOL_USE_REPROMPT_COUNT_KEY,
        )

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
