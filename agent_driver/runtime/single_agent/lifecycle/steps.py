"""Async step handlers for SingleAgentRunner (LLM, tools, finalize)."""

from __future__ import annotations

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.context import CompactionOrchestrator
from agent_driver.contracts.enums import (
    EventSeverity,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.llm.contracts import LlmResponse
from agent_driver.observability.provenance import build_provenance_summary
from agent_driver.runtime.control.dispatcher import drain_step_boundary_controls
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.lifecycle_hooks import (
    dispatch_finalize,
    dispatch_run_completed,
    dispatch_run_start,
)
from agent_driver.runtime.metadata_state import (
    get_loop_control_state,
    get_tool_loop_state,
)
from agent_driver.runtime.policy import policy_profile_from_metadata
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

# Hard backstop on goal-gate (rubric) revision loops, independent of any
# hook's own iteration budget — prevents an always-revising hook from looping.
_MAX_RUBRIC_REVISIONS = 10


def _hook_event_emitter(host, context: RunContext):
    """Emitter closure turning lifecycle-hook dispatch events into run events.

    Shared by run_start / finalize / run_completed dispatches (epic 024 phase C)
    so slow or timed-out hooks are visible in the event journal instead of an
    unexplained gap before the next runtime event.
    """

    def _emit_hook_event(event_type: str, payload: dict) -> None:
        host._emit(  # pylint: disable=protected-access
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType(event_type),
                payload=payload,
            )
        )

    return _emit_hook_event


# Tools whose calls are bookkeeping, not progress — refunded from the tool budget
# (epic 019 phase D). Keep in sync with the builtin planning/todo registrations.
_HOUSEKEEPING_TOOL_NAMES = frozenset(
    {"planning_state_update", "planning_progress", "todo_write"}
)


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
        # Epic 019 phase D (hermes iteration_budget.refund reference): housekeeping
        # calls (planning/todo bookkeeping) make no external progress and must not
        # burn the tool budget — a plan-disciplined agent would otherwise reach the
        # forced final earlier than a plan-less one. Tracked as a refund counter so
        # budget checks subtract it; the raw tool_calls count stays truthful.
        refunded = sum(
            1
            for envelope in result.envelopes
            if envelope.call.tool_name in _HOUSEKEEPING_TOOL_NAMES
        )
        if refunded:
            context.metadata["refunded_tool_calls"] = (
                int(context.metadata.get("refunded_tool_calls", 0) or 0) + refunded
            )
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
            self._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="trace_violation",
                action="warn",
                reason="node_contract_unsatisfiable_tools",
                status="failed",
                affected_tools=list(unsatisfiable),
                policy_id="node_contract",
            )
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
            allowed=(
                tuple(policy.allowed_tools)
                if policy.allowed_tools is not None
                else None
            ),
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
        await dispatch_run_start(
            self._deps.lifecycle_hooks,
            context,
            emit=_hook_event_emitter(self, context),
        )
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
        def _emit_control_warning(payload: dict) -> None:
            # Epic 030 A: control_kind_unsupported / invalid-payload → WARNING
            # (or an info context_usage_report) instead of a silent drop.
            severity = payload.get("severity", "warning")
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.WARNING,
                    payload=payload,
                    severity=(
                        EventSeverity.INFO
                        if severity == "info"
                        else EventSeverity.WARNING
                    ),
                )
            )

        applied_controls = drain_step_boundary_controls(
            context=context,
            store=self._deps.command_queue_store,
            abort_handle=context.abort_handle,
            emit=_emit_control_warning,
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
            self._emit_runtime_decision(
                context,
                kind="steering",
                trigger="control_applied",
                action="continue",
                reason="control_applied_at_step_boundary",
                affected_tools=[],
                redacted_metadata={
                    "control_id": item.control_id,
                    "kind": item.kind.value,
                    "priority": item.priority.value,
                },
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
            self._emit_runtime_decision(
                context,
                kind="force_final",
                trigger="finalize",
                action="force_final",
                reason=force_final_reason,
                policy_id="tool_loop",
            )
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
            self._emit_runtime_decision(
                context,
                kind="force_final",
                trigger="finalize",
                action="continue",
                reason=str(continuation_reason or "continuation_nudge"),
                policy_id="continuation_detector",
            )
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
        node_contract_reprompt = self._maybe_node_contract_required_tools_reprompt(
            context
        )
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
        evidence_block = _required_policy_evidence_block(
            context,
            events=[
                {
                    "event": event.type.value,
                    "run_id": event.run_id,
                    "attempt_id": event.attempt_id,
                    "seq": event.seq,
                    "data": event.payload,
                    "created_at": event.created_at,
                }
                for event in self._deps.event_log.list_for_run(context.run_id)
            ],
        )
        if evidence_block is not None:
            required_evidence = evidence_block.get("required_evidence", [])
            self._emit_runtime_decision(
                context,
                kind="evidence",
                trigger="finalize",
                action=str(evidence_block["action"]),
                reason=str(evidence_block["reason"]),
                status="applied",
                policy_id=str(evidence_block["policy_id"]),
                required_evidence=[
                    item for item in required_evidence if isinstance(item, str)
                ],
                redacted_metadata=evidence_block,
            )
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_FAILED,
                    payload={
                        "reason": TerminalReason.GUARDRAIL_BLOCKED.value,
                        "policy_id": evidence_block["policy_id"],
                    },
                )
            )
            self._emit_observe_policy_decisions(
                context,
                trigger="finalize_required_evidence",
            )
            output = self._build_output(
                context,
                TerminalResult(
                    status=RunStatus.FAILED,
                    reason=TerminalReason.GUARDRAIL_BLOCKED,
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
        revision = await dispatch_finalize(
            self._deps.lifecycle_hooks,
            context,
            answer=terminal_answer or "",
            emit=_hook_event_emitter(self, context),
            timeout=getattr(self._config, "finalize_hook_timeout", None),
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
        # Terminal side effects (memory persistence etc.): fires exactly once,
        # with the answer the user actually received. Hooks must schedule, not
        # block — this sits right before the terminal event is emitted.
        await dispatch_run_completed(
            self._deps.lifecycle_hooks,
            context,
            answer=terminal_answer or "",
            emit=_hook_event_emitter(self, context),
        )
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_COMPLETED,
                payload=completed_payload,
            )
        )
        self._emit_observe_policy_decisions(context, trigger="finalize")
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
            self._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="finalize",
                action="warn",
                reason="node_contract_no_tool_use_reprompt_budget_exhausted",
                status="failed",
                policy_id="node_contract",
            )
            return None
        text = (
            context.llm_response.message.content
            if context.llm_response is not None
            else ""
        )
        self._emit_runtime_decision(
            context,
            kind="retry",
            trigger="finalize",
            action="retry",
            reason="node_contract_no_tool_use_reprompt",
            policy_id="node_contract",
        )
        return _build_continuation_transition(
            context,
            text=text or "",
            nudge=nc.build_tool_use_reprompt(context.run_input),
            reason=nc._REPROMPT_REASON,
            count_key=nc.TOOL_USE_REPROMPT_COUNT_KEY,
        )

    def _maybe_node_contract_required_tools_reprompt(
        self, context: RunContext
    ) -> RuntimeStepResult | None:
        """Reactive guard: require declared terminal tools before finalization."""
        from agent_driver.runtime.single_agent import node_contract as nc

        if not nc.required_tools_violation_pending(context):
            return None
        if not nc.reprompt_budget_remaining(context):
            nc.stamp_required_tools_violation(context)
            self._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="finalize",
                action="warn",
                reason="node_contract_required_tools_reprompt_budget_exhausted",
                status="failed",
                affected_tools=list(
                    context.run_input.node_contract.finalize_when_tools
                ),
                policy_id="node_contract",
            )
            return None
        text = (
            context.llm_response.message.content
            if context.llm_response is not None
            else ""
        )
        self._emit_runtime_decision(
            context,
            kind="retry",
            trigger="finalize",
            action="retry",
            reason="node_contract_required_tools_reprompt",
            affected_tools=list(context.run_input.node_contract.finalize_when_tools),
            policy_id="node_contract",
        )
        return _build_continuation_transition(
            context,
            text=text or "",
            nudge=nc.build_required_tools_reprompt(context),
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


def _required_policy_evidence_block(
    context: RunContext,
    *,
    events: list[dict[str, object]],
) -> dict[str, object] | None:
    profile = policy_profile_from_metadata(context.run_input.app_metadata)
    if profile is None or profile.mode not in {"enforce", "fail_closed"}:
        return None
    enabled = set(profile.enabled_policy_ids)
    required = set(profile.required_evidence)
    if not required:
        return None
    metadata = {**context.run_input.app_metadata, **context.metadata}
    provenance = build_provenance_summary(
        events=events,
        metadata=metadata,
        required_evidence=list(required),
    )
    verdicts = provenance.get("contract_verdicts", {})
    violations = verdicts.get("violations") if isinstance(verdicts, dict) else {}
    if not isinstance(violations, dict):
        return None
    if (
        "source_evidence" in required
        and _policy_enabled(enabled, "required_source_evidence")
        and violations.get("missing_source_evidence") is True
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="required_source_evidence",
            action="mark_blocked",
            reason="required_source_evidence_missing",
            required_evidence=["source_evidence"],
        )
    if (
        "workbook_context" in required
        and _policy_enabled(enabled, "workbook_context_required")
        and not _workbook_context_observed(metadata)
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="workbook_context_required",
            action="mark_blocked",
            reason="required_workbook_context_missing",
            required_evidence=["workbook_context"],
        )
    if (
        "artifact_provenance" in required
        and _policy_enabled(enabled, "artifact_provenance_required")
        and violations.get("missing_artifact_provenance") is True
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="artifact_provenance_required",
            action="mark_blocked",
            reason="required_artifact_provenance_missing",
            required_evidence=["artifact_provenance"],
        )
    if (
        _policy_enabled(enabled, "side_effect_transaction_required")
        and _transaction_policy_enabled(profile.side_effect_rules, required)
        and violations.get("unsafe_side_effect_without_transaction_projection") is True
    ):
        return _evidence_block(
            profile_id=profile.profile_id,
            mode=profile.mode,
            policy_id="side_effect_transaction_required",
            action="rollback",
            reason="side_effect_transaction_missing",
            required_evidence=["side_effect_transactions"],
            redacted_metadata={
                "rollback_available": False,
                "rollback_projection": "missing",
            },
        )
    return None


def _policy_enabled(enabled: set[str], policy_id: str) -> bool:
    return not enabled or policy_id in enabled


def _transaction_policy_enabled(
    side_effect_rules: dict[str, object],
    required_evidence: set[str],
) -> bool:
    if "side_effect_transactions" in required_evidence:
        return True
    return side_effect_rules.get("require_transaction_projection") is True


def _workbook_context_observed(metadata: dict[str, object]) -> bool:
    workbook_context = metadata.get("workbook_context")
    if isinstance(workbook_context, dict):
        return True
    if isinstance(workbook_context, list) and any(
        isinstance(item, dict) for item in workbook_context
    ):
        return True
    rows = metadata.get("context_provenance")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind") or row.get("type")
        if kind in {"workbook", "workbook_context"}:
            return True
    return False


def _evidence_block(
    *,
    profile_id: str,
    mode: str,
    policy_id: str,
    action: str,
    reason: str,
    required_evidence: list[str],
    redacted_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "policy_id": policy_id,
        "policy_profile_id": profile_id,
        "policy_mode": mode,
        "action": action,
        "reason": reason,
        "required_evidence": required_evidence,
        "selected_policy_action": action,
        "enforcement": policy_id,
        **dict(redacted_metadata or {}),
    }
