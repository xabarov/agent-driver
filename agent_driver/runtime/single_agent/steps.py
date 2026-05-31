"""Async step handlers for SingleAgentRunner (LLM, tools, finalize)."""

from __future__ import annotations

from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.context import CompactionOrchestrator
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.control.dispatcher import drain_step_boundary_controls
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.research_session_contract import (
    FINAL_READINESS_ALLOWED,
    REPAIR_FINAL_MISSING_SOURCE_LINKS,
    REPAIR_INSUFFICIENT_SOURCE_DIVERSITY,
    REPAIR_MISSING_FETCHED_SOURCES,
    REPAIR_MISSING_RESEARCH_EVIDENCE,
    REPAIR_UNFINISHED_TODOS,
    build_research_session_contract_from_context,
)
from agent_driver.runtime.research_evidence import (
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    research_evidence_from_tool_results,
)
from agent_driver.runtime.single_agent.compaction_stage import (
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.continuation import analyze_continuation_intent
from agent_driver.runtime.single_agent.llm_step import execute_llm_call_step
from agent_driver.runtime.single_agent.step_planning import build_planning_snapshot
from agent_driver.runtime.single_agent.subagent_stage import (
    maybe_execute_subagent_group,
)
from agent_driver.runtime.single_agent.tool_stage import execute_tool_stage_step
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
        approved_call = context.metadata.pop("approved_tool_call", None)
        # A0.2 — only forward ``tool_gate`` when the caller actually set
        # one. Old executors and test mocks have ``(run_input,
        # llm_response)`` signatures and would reject an unknown kwarg;
        # the new contract documented in ``runtime/tools.py`` allows
        # ``tool_gate`` but we don't force it on the wire when None.
        gate_kwargs = (
            {"tool_gate": context.tool_gate} if context.tool_gate is not None else {}
        )
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
        existing_trace = context.metadata.get("tool_trace")
        if not isinstance(existing_trace, list):
            existing_trace = []
        existing_results = context.metadata.get("tool_results")
        if not isinstance(existing_results, list):
            existing_results = []
        existing_trace.extend(trace.model_dump(mode="json") for trace in result.traces)
        existing_results.extend(
            item.model_dump(mode="json") for item in result.envelopes
        )
        context.metadata["tool_trace"] = existing_trace
        context.metadata["tool_results"] = existing_results

    async def _execute_run_started(self, context: RunContext) -> RuntimeStepResult:
        from agent_driver.runtime.single_agent.step_planning import (
            apply_planning_state_seed_from_metadata,
        )

        apply_planning_state_seed_from_metadata(context)
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
        force_final_reason = context.metadata.get("force_final_answer_reason")
        if isinstance(force_final_reason, str) and force_final_reason:
            completed_payload["force_final_reason"] = force_final_reason
        continuation_reason = context.metadata.get("continuation_nudge_reason")
        if isinstance(continuation_reason, str) and continuation_reason:
            completed_payload["continuation_reason"] = continuation_reason
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
            context.metadata.update(
                {
                    "next_step": "llm_call",
                    "step_count": context.step_count,
                    "tool_calls": context.tool_calls,
                }
            )
            self._save_checkpoint(context, latest_output=None, node_id="finalize")
            self._maybe_fail_after_step("finalize")
            return continuation
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


def _maybe_build_continuation_transition(
    context: RunContext,
) -> RuntimeStepResult | None:
    """Continue when final text itself says there is a next step."""
    if context.llm_response is None:
        return None
    text = context.llm_response.message.content or ""
    contract = build_research_session_contract_from_context(
        context,
        assistant_text=text,
    )
    context.metadata["research_session_contract"] = contract.model_dump()
    readiness = contract.final_readiness
    if readiness.status != FINAL_READINESS_ALLOWED:
        reason_signature = ",".join(readiness.reasons)
        repair_count = int(context.metadata.get("contract_repair_nudge_count", 0))
        previous_signature = context.metadata.get("contract_repair_reason_signature")
        if repair_count >= 2 or (
            repair_count >= 1 and previous_signature == reason_signature
        ):
            context.metadata["final_readiness"] = "repair_exhausted"
            context.metadata["repair_required_reasons"] = list(readiness.reasons)
            return None
        from agent_driver.runtime.single_agent.continuation import ContinuationIntent

        intent = ContinuationIntent(True, "contract_repair_required")
        nudge = _research_contract_repair_nudge(context, readiness.reasons)
        context.metadata.pop("force_final_answer", None)
        context.metadata.pop("tool_choice_override", None)
        context.metadata.pop("force_final_answer_reason", None)
        _force_research_repair_tool_choice(context, readiness.reasons)
        context.metadata["contract_repair_reason_signature"] = reason_signature
        return _build_continuation_transition(
            context,
            text=text,
            nudge=nudge,
            reason=intent.reason,
            count_key="contract_repair_nudge_count",
        )
    count = int(context.metadata.get("continuation_nudge_count", 0))
    if count >= 2:
        return None
    intent = analyze_continuation_intent(text)
    if not intent.should_continue:
        return None
    nudge = (
        "Continue with the task. If you were about to proceed to the next "
        "step, do it now instead of only reporting progress. Reply in the "
        "user's language."
    )
    if intent.reason == "text_form_tool_call":
        nudge = (
            "The previous assistant message printed a tool call as text. Do not "
            "print JSON or <tool_call> blocks. If a tool is needed, call it using "
            "native function/tool-calling now; otherwise answer the user directly "
            "in the user's language."
        )
    return _build_continuation_transition(
        context,
        text=text,
        nudge=nudge,
        reason=intent.reason,
        count_key="continuation_nudge_count",
    )


def _build_continuation_transition(
    context: RunContext,
    *,
    text: str,
    nudge: str,
    reason: str,
    count_key: str,
) -> RuntimeStepResult:
    from agent_driver.contracts.enums import ChatRole
    from agent_driver.contracts.messages import ChatMessage

    protocol = context.metadata.get("protocol_messages")
    messages: list[dict[str, object]] = []
    if isinstance(protocol, list):
        messages = [item for item in protocol if isinstance(item, dict)]
    else:
        messages = [
            message.model_dump(mode="json") for message in context.run_input.messages
        ]
        if not messages:
            messages = [
                {"role": ChatRole.USER.value, "content": context.run_input.input or ""}
            ]
    messages.append(
        ChatMessage(role=ChatRole.ASSISTANT, content=text).model_dump(mode="json")
    )
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=nudge,
        ).model_dump(mode="json")
    )
    context.metadata["protocol_messages"] = messages
    context.metadata[count_key] = int(context.metadata.get(count_key, 0)) + 1
    context.metadata["continuation_nudge_reason"] = reason
    return RuntimeStepResult(next_step="llm_call")


def _force_research_repair_tool_choice(
    context: RunContext, reasons: tuple[str, ...]
) -> None:
    """Force the concrete research tool when a model tries to finish too early."""
    if REPAIR_MISSING_RESEARCH_EVIDENCE in reasons:
        if _tool_available_for_repair(context, "web_search"):
            context.metadata["tool_choice_override"] = {
                "type": "tool",
                "name": "web_search",
            }
            return
    if REPAIR_INSUFFICIENT_SOURCE_DIVERSITY in reasons:
        if _tool_available_for_repair(context, "web_search"):
            context.metadata["tool_choice_override"] = {
                "type": "tool",
                "name": "web_search",
            }
            return
    if REPAIR_MISSING_FETCHED_SOURCES in reasons:
        if _tool_available_for_repair(context, "web_fetch"):
            context.metadata["tool_choice_override"] = {
                "type": "tool",
                "name": "web_fetch",
            }
            return
    if REPAIR_UNFINISHED_TODOS in reasons:
        if _research_evidence_ready_for_final_repair(context):
            context.metadata["tool_choice_override"] = "none"
            context.metadata["force_final_answer"] = True
            context.metadata["force_final_answer_reason"] = (
                "contract_repair_final_answer"
            )
            return
        if _tool_available_for_repair(context, "todo_write"):
            context.metadata["tool_choice_override"] = {
                "type": "tool",
                "name": "todo_write",
            }


def _tool_available_for_repair(context: RunContext, tool_name: str) -> bool:
    policy = context.run_input.tool_policy
    denied = set(policy.denied_tools or [])
    if tool_name in denied:
        return False
    allowed = policy.allowed_tools
    if allowed is not None and tool_name not in set(allowed):
        return False
    effective = context.metadata.get("effective_tool_names")
    if isinstance(effective, (list, tuple, set)) and tool_name not in set(effective):
        return False
    return True


def _research_evidence_ready_for_final_repair(context: RunContext) -> bool:
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if not isinstance(task_contract, dict):
        return False
    if task_contract.get("requires_research") is not True:
        return False
    evidence = research_evidence_from_tool_results(context.metadata.get("tool_results"))
    if task_contract.get("research_depth") == "source_verified_report":
        if not _tool_available_for_repair(context, "web_fetch"):
            return evidence.search_calls > 0 or evidence.fetch_calls > 0
        if evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES and (
            evidence.search_calls > 0 or evidence.fetch_calls > 0
        ):
            return True
        return evidence.source_verified(
            required_fetches=SOURCE_VERIFIED_FETCHES,
            required_domains=SOURCE_VERIFIED_DOMAINS,
        )
    return evidence.search_calls > 0 or evidence.fetch_calls > 0


def _research_contract_repair_nudge(
    context: RunContext, reasons: tuple[str, ...]
) -> str:
    """Return a compact one-shot repair instruction for contract violations."""
    fragments: list[str] = []
    if REPAIR_UNFINISHED_TODOS in reasons:
        fragments.append(
            "the visible todo/checklist still has pending or in-progress items"
        )
    if REPAIR_MISSING_RESEARCH_EVIDENCE in reasons:
        fragments.append("the user requested research but no web evidence was used")
    if REPAIR_MISSING_FETCHED_SOURCES in reasons:
        fragments.append(
            "source-verified work needs fetched/read pages, not search results only"
        )
    if REPAIR_INSUFFICIENT_SOURCE_DIVERSITY in reasons:
        evidence = research_evidence_from_tool_results(
            context.metadata.get("tool_results")
        )
        domains = ", ".join(evidence.unique_domains)
        message = "the fetched evidence needs at least two distinct domains"
        if domains:
            message += (
                f"; already fetched domain(s): {domains}; search for and fetch "
                "a concrete source outside those domains"
            )
        fragments.append(message)
    if REPAIR_FINAL_MISSING_SOURCE_LINKS in reasons:
        fragments.append("the final answer must include visible source links")
    reason_text = (
        "; ".join(fragments) if fragments else "the run contract is incomplete"
    )
    return (
        "Contract repair required before the final answer: "
        f"{reason_text}. Continue now using only the real available tools "
        "(todo_write, web_search, web_fetch, python, agent_tool when useful). "
        "Update the visible todo state when a step is done, cite fetched URLs in "
        "the final response, and reply in the user's language."
    )


__all__ = ["SingleAgentStepMixin"]
