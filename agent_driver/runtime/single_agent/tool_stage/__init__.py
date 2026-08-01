"""Tool stage execution and transitions."""

from __future__ import annotations

import json
from typing import Any, Protocol

from agent_driver.contracts.enums import (
    AgentProfile,
    ChatRole,
    GuardrailDecision,
    RuntimeEventType,
    ToolPolicyDecision,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.scaffolding import scaffolding_metadata
from agent_driver.llm.contracts import LlmFinishReason
from agent_driver.llm.reasoning_hygiene import strip_leading_think_block
from agent_driver.llm.tool_call_parser import strip_text_form_tool_calls
from agent_driver.observability.source_evidence import source_evidence_from_tool_result
from agent_driver.prompts import force_final_answer_tool_message
from agent_driver.runtime.artifact_events import artifact_event_from_tool_result
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.metadata_state import (
    get_tool_loop_state,
)
from agent_driver.runtime.research_artifacts import (
    persist_deep_research_claims_matrix,
    persist_deep_research_source_ledger,
)
from agent_driver.runtime.research_evidence import (
    research_evidence_from_tool_results,
    research_source_ledger_from_tool_results,
    rollup_child_source_ledgers,
)
from agent_driver.runtime.research_session_contract import (
    child_source_ledgers_from_context,
)
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    append_todo_progress_hint_after_substantive_tool,
    increment_tool_loops_since_todo_write,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.lifecycle.pending import (
    pending_interrupt_from_execution_result,
    serialize_pending_interrupt,
)
from agent_driver.runtime.single_agent.planning.state import (
    apply_planning_updates_from_envelopes,
    build_planning_snapshot,
    update_planning_state_from_tool_results,
)
from agent_driver.runtime.single_agent.tool_stage.observations import (
    build_observations_from_tool_result,
)
from agent_driver.runtime.single_agent.tool_stage.planning import (
    emit_plan_lifecycle_events,
)
from agent_driver.runtime.single_agent.tool_stage.research import (
    append_web_fetch_duplicate_guard,
    append_web_fetch_verification_hint,
    force_web_fetch_for_source_verified_research,
)
from agent_driver.runtime.single_agent.tool_stage.subagents import (
    apply_agent_tool_spawn_requests,
    apply_subagent_control_tool_outputs,
)
from agent_driver.runtime.single_agent.tool_stage.recovery import (
    _append_denial_recovery_message,
    _append_disallowed_management_tool_recovery_hint,
    _append_python_policy_recovery_hint,
    _append_tool_call_parse_error_feedback,
    _append_unknown_tool_recovery_message,
)
from agent_driver.runtime.single_agent.tool_stage.protocol_messages import (
    _compact_generic_tool_payload_for_protocol,
    _compact_tool_payload_for_protocol,
    _is_drop_candidate_assistant_message,
    _load_protocol_messages,
    _normalize_protocol_messages,
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

from agent_driver.runtime.single_agent.tool_stage.deep_research import (
    _clamp_deep_research_initial_subagent_batch,
    _clamp_deep_research_parent_artifact_batch,
    _coerce_deep_research_artifact_repair_batch,
    _coerce_deep_research_parent_synthesis_write,
    _repair_deep_research_parent_file_write_args,
    _suppress_deep_research_terminal_tool_calls,
)

from agent_driver.runtime.single_agent.finalization.answer_recovery import (
    final_content_unusable,
    is_degenerate_refusal,
)

# Epic 015: bound the empty-answer re-prompt so a provider that keeps returning empty can't spin.
from agent_driver.runtime.single_agent.tool_stage.guards import (  # noqa: F401 - re-export for compat
    _POLICY_READ_LIKE_TOOLS,
    _PROGRESS_ONLY_TOOL_NAMES,
    _TOOL_FAILURE_GUARD_THRESHOLD,
    _current_no_progress_repeat,
    _deliverable_request_should_force_final,
    _force_final_reason,
    _has_repeated_recent_tool_call,
    _has_successful_python_result,
    _maybe_enforce_tool_loop_policy,
    _maybe_force_final_answer,
    _python_reliability_request_active,
    _python_reliability_request_pending,
    _python_request_should_force_final,
    _refresh_force_final_controls,
    _resolved_budget,
    _should_force_final_answer,
    _tool_loop_policy_threshold,
    _tool_no_progress_key,
    _update_tool_failure_guard,
)

_MAX_EMPTY_ANSWER_RETRIES = 1
# A wrong-language / canned-refusal degenerate answer is stochastic (deepseek ~60% on some queries), so
# allow a few bounded retries — each re-prompt is a fresh chance to answer correctly from the same
# context (3 retries takes the residual ~0.6^4 ≈ 13%). Only the degenerate minority pays the latency.
_MAX_DEGENERATE_ANSWER_RETRIES = 3
# Epic 042 B: a provider that signals finish_reason=tool_calls but ships an EMPTY
# tool_calls array (observed: opus-4.8 / sonnet-4.5 on GitHub Copilot) must be
# re-prompted for the call, not finalized on its narration — otherwise an
# unattended job "succeeds" at tool_turns=0. Bounded so a broken provider can't spin.
_MAX_EMPTY_TOOL_CALLS_REPROMPTS = 3

_force_web_fetch_for_source_verified_research = (
    force_web_fetch_for_source_verified_research
)


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
    def _build_paused_output(
        self, context: RunContext, result: ToolExecutionResult
    ) -> Any: ...
    def _emit(self, event: EventSpec) -> None: ...
    def _emit_runtime_decision(
        self,
        context: RunContext,
        *,
        kind: str,
        trigger: str,
        action: str,
        reason: str,
        status: str = "applied",
        goal_id: str | None = None,
        policy_id: str | None = None,
        budget: dict[str, object] | None = None,
        affected_tools: list[str] | None = None,
        required_evidence: list[str] | None = None,
        observed_evidence: list[str] | None = None,
        product_tags: list[str] | None = None,
        redacted_metadata: dict[str, object] | None = None,
    ) -> Any: ...
    def _save_checkpoint(
        self, context: RunContext, *, latest_output: Any, node_id: str
    ) -> Any: ...
    def _maybe_fail_after_step(self, step_name: str) -> None: ...
    async def _maybe_execute_subagent_group(self, context: RunContext) -> None: ...


async def execute_tool_stage_step(
    host: ToolStageHost, context: RunContext
) -> RuntimeStepResult:
    """Execute tool stage and route to interrupt, code-agent loop, or finalize."""
    _suppress_deep_research_terminal_tool_calls(context)
    _clamp_deep_research_initial_subagent_batch(context)
    _coerce_deep_research_parent_synthesis_write(context)
    _repair_deep_research_parent_file_write_args(context)
    _coerce_deep_research_artifact_repair_batch(context)
    _clamp_deep_research_parent_artifact_batch(context)
    _emit_tool_started_if_needed(host, context)
    # Epic 025: liveness heartbeat over the whole tool stage — a wedged tool
    # without TOOL_PROGRESS opt-in is otherwise a silent stage.
    from agent_driver.runtime.single_agent.lifecycle.events import (  # noqa: PLC0415
        stage_wait_heartbeat,
    )

    async with stage_wait_heartbeat(
        host,
        context,
        stage="tool_stage",
        interval=getattr(getattr(host, "_config", None), "stage_heartbeat_seconds", None),
    ):
        result = await host._tool_result_with_approved_override(context)
    host._store_tool_stage_outputs(context, result)
    _post_process_tool_result(host, context, result)
    emit_plan_lifecycle_events(host, context, result)
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
    _record_skill_invocations(host, context, result)
    apply_agent_tool_spawn_requests(context, result)
    apply_subagent_control_tool_outputs(host, context, result)
    _update_zero_result_policy(context, result)
    _refresh_force_final_controls(context)
    force_web_fetch_for_source_verified_research(context)
    if not planning_updated:
        update_planning_state_from_tool_results(context)


def _record_skill_invocations(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    """Persist compact skill invocation records from skill_view outputs."""
    tool_state = get_tool_loop_state(context)
    for envelope in result.envelopes:
        if envelope.call.tool_name != "skill_view":
            continue
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        invocation = structured.get("skill_invocation")
        if not isinstance(invocation, dict):
            continue
        payload = dict(invocation)
        if not payload.get("tool_call_id"):
            payload["tool_call_id"] = envelope.call.tool_call_id
        tool_state.append_skill_invocation(payload)
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.SKILL_INVOKED,
            payload={
                "name": payload.get("name"),
                "path": payload.get("path"),
                "digest": payload.get("digest"),
                "trusted": payload.get("trusted"),
                "agent_id": payload.get("agent_id"),
                "content_kind": payload.get("content_kind"),
                "relative_file": payload.get("relative_file"),
                "tool_call_id": payload.get("tool_call_id"),
            },
        )


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
    if continue_with_llm:
        finalize_now = await _maybe_finalize_from_tool_evidence(host, context, result)
        if finalize_now:
            continue_with_llm = False
    # Epic 015: bounded re-prompt when a run is about to finalize a DEGENERATE answer rather than a real
    # one. Two cases: (a) an EMPTY answer on a pure-text run (deepseek streaming empty STOP) — gated on
    # zero tool calls so a tool-evidence/early-finalize empty text is untouched; (b) a canned/wrong-
    # language refusal («作为一个人工智能…» to a Russian question, or «as an AI language model I haven't
    # learned…») — retried regardless of tool calls, since the model can answer correctly from the same
    # context on another draw. Each bounded so a persistently-degenerate provider can't spin.
    if not continue_with_llm and context.llm_response is not None:
        content_text = (context.llm_response.message.content or "").strip()
        input_text = str(getattr(context.run_input, "input", "") or "")
        empty_retries = int(context.metadata.get("empty_answer_retry_count", 0))
        if (
            not content_text
            and context.tool_calls == 0
            and empty_retries < _MAX_EMPTY_ANSWER_RETRIES
        ):
            context.metadata["empty_answer_retry_count"] = empty_retries + 1
            continue_with_llm = True
        elif content_text and is_degenerate_refusal(content_text, input_text):
            refusal_retries = int(
                context.metadata.get("degenerate_answer_retry_count", 0)
            )
            if refusal_retries < _MAX_DEGENERATE_ANSWER_RETRIES:
                context.metadata["degenerate_answer_retry_count"] = refusal_retries + 1
                continue_with_llm = True
    # Epic 042 B: empty tool_calls contract violation. The provider said it wanted a
    # tool (finish_reason=tool_calls) but shipped no call — no envelopes ran and no
    # call parsed. Re-prompt for the call instead of finalizing the NARRATION; a
    # successful tool round resets the counter (below). Gated on the content NOT being
    # a usable answer: a model that answered substantively despite a spurious
    # tool_calls finish reason is finalized as-is (preserves the 015 baseline).
    if (
        not continue_with_llm
        and context.llm_response is not None
        and context.llm_response.finish_reason == LlmFinishReason.TOOL_CALLS
        and not result.envelopes
        and not extract_planned_tool_calls(context.llm_response)
        # Not a provider contract violation if the runtime itself suppressed a call
        # the provider DID ship (forced-final / budget winding down) — that path moves
        # the calls to ``suppressed_planned_tool_calls``. Only a genuinely empty
        # provider array (no suppression marker) is the (b) violation.
        and not context.llm_response.metadata.get("suppressed_planned_tool_calls")
        and final_content_unusable(
            (context.llm_response.message.content or "").strip(),
            str(getattr(context.run_input, "input", "") or ""),
        )
    ):
        empty_tc_reprompts = int(
            context.metadata.get("empty_tool_calls_reprompt_count", 0)
        )
        if empty_tc_reprompts < _MAX_EMPTY_TOOL_CALLS_REPROMPTS:
            context.metadata["empty_tool_calls_reprompt_count"] = empty_tc_reprompts + 1
            continue_with_llm = True
            emit_step_event(
                host,
                context,
                event_type=RuntimeEventType.WARNING,
                payload={
                    "warning": (
                        "Provider signalled a tool call (finish_reason=tool_calls) "
                        "but shipped an empty tool_calls array; re-prompting for the "
                        "call instead of finalizing the narration."
                    ),
                    "signal_id": "empty_tool_calls_contract_violation",
                    "severity": "warning",
                    "reprompt_count": empty_tc_reprompts + 1,
                },
            )
    elif result.envelopes:
        # A real tool round happened — the model can make progress again.
        context.metadata.pop("empty_tool_calls_reprompt_count", None)
    loop_iterations = int(context.metadata.get("tool_loop_iterations", 0))
    if continue_with_llm:
        loop_iterations += 1
        increment_tool_loops_since_todo_write(context)
    _update_tool_failure_guard(host, context, result)
    if continue_with_llm and context.run_input.agent_profile != AgentProfile.CODE_AGENT:
        _maybe_force_final_answer(context)
        _maybe_enforce_tool_loop_policy(host, context, result)
        force_web_fetch_for_source_verified_research(context)
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


async def _maybe_finalize_from_tool_evidence(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> bool:
    """Layer C: finalize directly from tool evidence, skipping the next LLM pass.

    Two opt-in triggers: the declarative ``finalize_when_tools`` (every listed tool
    has a successful envelope) and the programmatic ``on_tool_evidence`` lifecycle
    hook (a host returns :class:`FinalizeNow`). When either fires we stash an
    early-finalize answer so the finalize step can build the terminal output without
    another model turn. Returns ``True`` when the run should finalize now.
    """
    from agent_driver.runtime.lifecycle_hooks import dispatch_tool_evidence
    from agent_driver.runtime.single_agent import node_contract as nc

    if nc.declarative_finalize_satisfied(context):
        nc.set_early_finalize(
            context,
            answer=nc.build_evidence_answer(context),
            reason="finalize_when_tools_satisfied",
        )
        return True
    def _emit_hook_event(event_type: str, payload: dict) -> None:
        host._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType(event_type),
                payload=payload,
            )
        )

    directive = await dispatch_tool_evidence(
        host._deps.lifecycle_hooks,
        context,
        result.envelopes,
        emit=_emit_hook_event,
    )
    if directive is not None:
        nc.set_early_finalize(context, answer=directive.answer, reason=directive.reason)
        return True
    return False


def _emit_tool_completed_if_needed(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    if not result.traces:
        return
    planned_calls = (
        extract_planned_tool_calls(context.llm_response) if context.llm_response else []
    )
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
            if envelope.call.tool_name == "glob_search" and isinstance(
                structured.get("results"), list
            ):
                preview_paths = [
                    str(item) for item in structured["results"] if isinstance(item, str)
                ][:5]
            elif envelope.call.tool_name == "web_search" and isinstance(
                structured.get("result_preview_urls"), list
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
            "args": (
                args_by_call_id.get(trace.tool_call_id)
                if isinstance(trace.tool_call_id, str) and trace.tool_call_id
                else (fallback_args[index] if index < len(fallback_args) else {})
            ),
            "status": trace.status.value,
            "result_summary": trace.result_summary,
            "output_preview": (
                trace.result_summary[:240]
                if isinstance(trace.result_summary, str)
                else None
            ),
            "structured_output": None,
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
            "risk": trace.risk.value,
            "side_effect": trace.side_effect.value,
            "approval_mode": trace.approval_mode.value,
        }
        if index < len(result.envelopes):
            envelope = result.envelopes[index]
            structured = envelope.structured_output
            if isinstance(structured, dict):
                row["structured_output"] = structured
                remediation = structured.get("remediation")
                if isinstance(remediation, str) and remediation.strip():
                    row["remediation"] = remediation.strip()
                persisted_artifact = structured.get("persisted_artifact")
                if isinstance(persisted_artifact, dict):
                    row["persisted_artifact"] = dict(persisted_artifact)
                    workspace_path = persisted_artifact.get("workspace_path")
                    if isinstance(workspace_path, str) and workspace_path:
                        row["result_preview_paths"] = [workspace_path]
                if trace.status.value == "completed":
                    sources = source_evidence_from_tool_result(
                        tool_name=envelope.call.tool_name,
                        structured_output=structured,
                        tool_call_id=envelope.call.tool_call_id,
                    )
                    if sources:
                        row["sources"] = sources
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
    _emit_tool_policy_runtime_decisions(host, context, result)
    _emit_artifact_events_from_tool_result(host, context, result)
    tool_results_rows = get_tool_loop_state(context).tool_results()
    child_ledgers = child_source_ledgers_from_context(context)
    merged_ledger, _ = rollup_child_source_ledgers(
        research_source_ledger_from_tool_results(tool_results_rows),
        research_evidence_from_tool_results(tool_results_rows),
        child_ledgers,
    )
    source_ledger = merged_ledger.model_dump()
    parent_used_source_tool = any(
        row["tool_name"]
        in {"web_search", "web_fetch", "source_read", "pdf_read", "browser_read"}
        for row in tools
        if isinstance(row.get("tool_name"), str)
    )
    # Persist when the parent itself read sources this turn OR when joined
    # children contributed verified reads — otherwise a delegating parent that
    # never fetches would never emit sources.jsonl/claims.jsonl despite real
    # child evidence.
    child_contributed_reads = bool(child_ledgers) and bool(
        merged_ledger.verified_reads
        or merged_ledger.blocked_reads
        or merged_ledger.failed_reads
    )
    if parent_used_source_tool or child_contributed_reads:
        source_artifact = persist_deep_research_source_ledger(context, source_ledger)
        if source_artifact is not None:
            source_ledger["artifact"] = {
                "path": source_artifact["path"],
                "record_count": source_artifact["record_count"],
                "size_bytes": source_artifact["size_bytes"],
            }
            emit_step_event(
                host,
                context,
                event_type=(
                    RuntimeEventType.ARTIFACT_CREATED
                    if source_artifact.get("created") is True
                    else RuntimeEventType.ARTIFACT_UPDATED
                ),
                payload={
                    "path": source_artifact["path"],
                    "kind": source_artifact["kind"],
                    "operation": source_artifact["operation"],
                    "tool_name": "source_ledger",
                    "size_bytes": source_artifact["size_bytes"],
                    "bytes": source_artifact["bytes"],
                    "record_count": source_artifact["record_count"],
                },
            )
        claims_artifact = persist_deep_research_claims_matrix(context, source_ledger)
        if claims_artifact is not None:
            emit_step_event(
                host,
                context,
                event_type=(
                    RuntimeEventType.ARTIFACT_CREATED
                    if claims_artifact.get("created") is True
                    else RuntimeEventType.ARTIFACT_UPDATED
                ),
                payload={
                    "path": claims_artifact["path"],
                    "kind": claims_artifact["kind"],
                    "operation": claims_artifact["operation"],
                    "tool_name": "claims_matrix",
                    "size_bytes": claims_artifact["size_bytes"],
                    "bytes": claims_artifact["bytes"],
                    "record_count": claims_artifact["record_count"],
                    "verified_count": claims_artifact["verified_count"],
                    "unsupported_count": claims_artifact["unsupported_count"],
                    "inaccessible_count": claims_artifact.get("inaccessible_count", 0),
                },
            )
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.SOURCE_LEDGER_UPDATED,
            payload=source_ledger,
        )


def _emit_tool_policy_runtime_decisions(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    for envelope in result.envelopes:
        tool_name = envelope.call.tool_name
        metadata: dict[str, object] = {
            "tool_call_id": envelope.call.tool_call_id,
            "policy_decision": envelope.decision.value,
            "guardrail_decision": envelope.guardrail_decision.value,
        }
        if envelope.error is not None:
            metadata["error_code"] = envelope.error.code
        if envelope.decision == ToolPolicyDecision.DENY:
            host._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="tool_denied",
                action="block",
                reason="tool_policy_denied",
                policy_id="tool_policy",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )
        elif envelope.decision == ToolPolicyDecision.INTERRUPT:
            host._emit_runtime_decision(
                context,
                kind="approval",
                trigger="tool_denied",
                action="interrupt",
                reason="tool_policy_interrupt",
                policy_id="tool_policy",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )
        if envelope.guardrail_decision == GuardrailDecision.BLOCK:
            host._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="tool_denied",
                action="block",
                reason="tool_guardrail_blocked",
                policy_id="tool_guardrail",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )
        elif envelope.guardrail_decision == GuardrailDecision.SANITIZE:
            host._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="tool_completed",
                action="warn",
                reason="tool_guardrail_sanitized",
                policy_id="tool_guardrail",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )


def _emit_artifact_events_from_tool_result(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    for envelope in result.envelopes:
        event = artifact_event_from_tool_result(context, envelope)
        if event is None:
            continue
        event_type, payload = event
        emit_step_event(host, context, event_type=event_type, payload=payload)


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


def _update_tool_protocol_messages(
    context: RunContext, result: ToolExecutionResult
) -> None:
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls:
        return
    messages = _load_protocol_messages(context)
    assistant_tool_calls = []
    for index, planned_call in enumerate(planned_calls):
        protocol_call = planned_call
        if index < len(result.envelopes):
            executed_call = result.envelopes[index].call
            if executed_call.metadata.get("tool_alias_normalized") is True:
                protocol_call = executed_call
        assistant_tool_calls.append(
            {
                "id": protocol_call.tool_call_id or f"call_{index}",
                "type": "function",
                "function": {
                    "name": protocol_call.tool_name,
                    "arguments": json.dumps(protocol_call.args, ensure_ascii=True),
                },
            }
        )
    assistant_metadata: dict[str, Any] = {"tool_calls": assistant_tool_calls}
    reasoning_details = response.metadata.get("provider_reasoning_details")
    if isinstance(reasoning_details, list) and reasoning_details:
        assistant_metadata["reasoning_details"] = reasoning_details
    reasoning = response.metadata.get("provider_reasoning")
    if (
        "reasoning_details" not in assistant_metadata
        and isinstance(reasoning, str)
        and reasoning
    ):
        assistant_metadata["reasoning"] = reasoning
    # Epic 043 A: inline CoT (`<think>…`) must never enter replayable history —
    # an assistant turn exposing its own reasoning can poison every later call.
    content, think_stripped = strip_leading_think_block(
        strip_text_form_tool_calls(response.message.content or "")
    )
    if think_stripped:
        assistant_metadata["inline_reasoning_stripped_chars"] = think_stripped
    messages.append(
        ChatMessage(
            role=ChatRole.ASSISTANT,
            content=content,
            metadata=assistant_metadata,
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
    _append_unknown_tool_recovery_message(context, result, messages)
    _append_disallowed_management_tool_recovery_hint(context, result, messages)
    _append_python_policy_recovery_hint(context, result, messages)
    _append_tool_call_parse_error_feedback(context, result, messages)
    append_todo_progress_hint_after_substantive_tool(context, result, messages)
    append_web_fetch_verification_hint(context, result, messages)
    append_web_fetch_duplicate_guard(context, result, messages)
    if context.metadata.get("force_final_answer"):
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=force_final_answer_tool_message(),
                metadata=scaffolding_metadata("force_final_answer"),
            )
        )
    _normalize_protocol_messages(messages)
    context.metadata["protocol_messages"] = [
        item.model_dump(mode="json") for item in messages
    ]


def _update_zero_result_policy(
    context: RunContext, result: ToolExecutionResult
) -> None:
    zero_streak = int(context.metadata.get("web_search_zero_streak", 0))
    saw_web_search = False
    for envelope in result.envelopes:
        if envelope.call.tool_name != "web_search":
            continue
        saw_web_search = True
        if envelope.decision != ToolPolicyDecision.ALLOW or envelope.error is not None:
            continue
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
        get_tool_loop_state(context).force_final_answer(
            reason="web_search_zero_results"
        )


__all__ = [
    "ToolStageHost",
    "_clamp_deep_research_parent_artifact_batch",
    "_coerce_deep_research_artifact_repair_batch",
    "_force_web_fetch_for_source_verified_research",
    "_should_force_final_answer",
    "_suppress_deep_research_terminal_tool_calls",
    "_update_tool_protocol_messages",
    "execute_tool_stage_step",
]
