"""Build AgentRunOutput for terminal and paused states."""

from __future__ import annotations

from typing import Any

from agent_driver.context import (
    planning_state_event,
    planning_step_event,
    split_preview_and_artifact,
)
from agent_driver.context.breakdown import estimate_context_breakdown
from agent_driver.context.compaction import (
    extract_session_memory,
    load_session_memory,
    save_session_memory,
)
from agent_driver.contracts.context import (
    ContextArtifactRef,
    PlanningState,
    PlanningStep,
    SessionRef,
    SessionTurn,
    TurnDigest,
)
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.contracts.interrupts import ApprovalPayload, InterruptRequest
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunOutput, ContextDiagnostics
from agent_driver.llm.reasoning_hygiene import strip_leading_think_block
from agent_driver.llm.tool_call_parser import strip_text_form_tool_calls
from agent_driver.observability.source_evidence import (
    merge_source_evidence,
    source_evidence_from_tool_result,
)
from agent_driver.runtime.metadata_state import (
    get_compaction_runtime_state,
    get_loop_control_state,
    get_planning_runtime_state,
    get_streaming_runtime_state,
)
from agent_driver.runtime.research_artifacts import (
    deep_research_report_artifact_exists,
    deep_research_source_ledger_artifact_exists,
)
from agent_driver.runtime.research_evidence import (
    research_source_ledger_from_tool_results,
)
from agent_driver.runtime.control.live_messages import (
    live_message_receipt,
    live_message_transition_event,
)
from agent_driver.runtime.single_agent.finalization.answer_recovery import (
    recover_degenerate_terminal_answer,
)
from agent_driver.runtime.single_agent.finalization.output_builders import (
    build_memory_audit,
    build_memory_projection_for_context,
    collect_tool_trace,
    list_dict_metadata,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerDeps,
    TerminalResult,
)
from agent_driver.subagents import summarize_child_runs_for_parent


def _safe_context_breakdown(context: RunContext) -> dict[str, Any]:
    """Per-category breakdown of the run's message state; fail-open to {} (epic 044 C).

    Prefers the assembled ``protocol_messages`` (system + conversation + tool rows as
    the request actually saw them); falls back to the run-input messages, then to a
    synthesized user turn from ``input`` so a tool-less run still reports its prompt.
    """
    try:
        assembled = context.metadata.get("context_breakdown")
        if isinstance(assembled, dict) and assembled:
            return assembled
        protocol = context.metadata.get("protocol_messages")
        if isinstance(protocol, list) and protocol:
            return estimate_context_breakdown(protocol)
        messages = list(context.run_input.messages or ())
        if not messages and context.run_input.input:
            messages = [{"role": "user", "content": context.run_input.input}]
        return estimate_context_breakdown(messages)
    except Exception:  # pylint: disable=broad-exception-caught - observability only
        return {}


def _control_matches_run(item: Any, context: RunContext) -> bool:
    """Whether a queued control targets this run (run/thread/agent id match)."""
    if item.run_id is not None and item.run_id == context.run_id:
        return True
    if item.thread_id is not None and item.thread_id == context.run_input.thread_id:
        return True
    if item.agent_id is not None and item.agent_id == context.run_input.agent_id:
        return True
    return False


def _deep_research_terminal_handoff_ready(context: RunContext) -> bool:
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    deep_contract = isinstance(task_contract, dict) and (
        task_contract.get("research_mode") == "deep"
        or task_contract.get("research_depth") == "deep_parallel_research"
        or task_contract.get("research_depth") == "source_verified_report"
    )
    metadata_enabled = (
        isinstance(
            context.run_input.tool_policy.metadata.get("deep_research_mode"), dict
        )
        or context.run_input.app_metadata.get("research_mode") == "deep"
    )
    if not (deep_contract or metadata_enabled):
        return False
    return deep_research_report_artifact_exists(
        context
    ) and deep_research_source_ledger_artifact_exists(context)


def _is_concise_deep_research_handoff(answer: str) -> bool:
    if len(answer) > 800:
        return False
    return "research/report.md" in answer


class SingleAgentOutputMixin:
    """Mixin: normalized run output envelopes."""

    _deps: RunnerDeps

    def _persist_session_artifacts(
        self,
        *,
        context: RunContext,
        answer: str | None,
        artifact_refs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist session row + digest and return normalized digest refs."""
        session_id = context.run_input.thread_id or context.run_id
        self._deps.session_store.upsert_session(
            SessionRef(
                session_id=session_id,
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                workspace_id=context.run_input.workspace_id,
                metadata={"agent_id": context.run_input.agent_id},
            )
        )
        turn_index = len(self._deps.session_store.list_turns(session_id))
        self._deps.session_store.append_turn(
            SessionTurn(
                session_id=session_id,
                turn_index=turn_index,
                message=ChatMessage(role="assistant", content=answer or ""),
                metadata={"run_id": context.run_id},
            )
        )
        digest = self._deps.session_store.save_digest(
            session_id,
            TurnDigest(
                digest_id=f"digest_{context.run_id}_{turn_index}",
                turn_index=turn_index,
                summary=(answer or "")[:200] or "no_answer",
                references=[ref.get("artifact_id", "") for ref in artifact_refs if ref],
                metadata={
                    "run_id": context.run_id,
                    "thread_id": context.run_input.thread_id,
                },
            ),
        )
        digest_ref = {"digest_id": digest.digest_id, "turn_index": digest.turn_index}
        context.metadata["digest_refs"] = [digest_ref]
        self._maybe_update_session_memory(context=context, session_id=session_id)
        return [digest_ref]

    def _maybe_update_session_memory(
        self, *, context: RunContext, session_id: str
    ) -> None:
        """Refresh durable session memory from turn digests when threshold is met."""
        previous = load_session_memory(
            artifact_store=self._deps.artifact_store,
            session_id=session_id,
        )
        digests = self._deps.session_store.list_digests(session_id)
        extraction = extract_session_memory(
            session_id=session_id,
            digests=digests,
            previous=previous,
        )
        if extraction.updated and extraction.memory is not None:
            save_session_memory(
                artifact_store=self._deps.artifact_store,
                memory=extraction.memory,
            )
        get_compaction_runtime_state(context).set_session_memory_extraction(
            {
                "updated": extraction.updated,
                "reason": extraction.reason,
                "considered_digest_ids": list(extraction.considered_digest_ids),
                "last_summarized_turn_index": (
                    extraction.memory.last_summarized_turn_index
                    if extraction.memory is not None
                    else (
                        previous.last_summarized_turn_index
                        if previous is not None
                        else None
                    )
                ),
            }
        )

    def _emit_planning_events(self, context: RunContext) -> None:
        """Emit dedicated planning events if state exists in metadata."""
        planning_state = get_planning_runtime_state(context)
        step_payload = planning_state.dict_or_none("planning_step")
        if isinstance(step_payload, dict):
            self._deps.event_log.append(
                planning_step_event(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    seq=self._next_seq(context.run_id),
                    step=PlanningStep.model_validate(step_payload),
                )
            )
        state_payload = planning_state.planning_state()
        if isinstance(state_payload, dict):
            self._deps.event_log.append(
                planning_state_event(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    seq=self._next_seq(context.run_id),
                    state=PlanningState.model_validate(state_payload),
                )
            )

    def _normalize_context_artifacts(
        self, run_id: str, refs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach refs into context store and normalize payload."""
        normalized: list[dict[str, Any]] = []
        for ref_payload in refs:
            ref = ContextArtifactRef.model_validate(ref_payload)
            self._deps.context_store.attach_artifact(run_id, ref)
            normalized.append(ref.model_dump(mode="json"))
        return normalized

    def _metadata_with_artifact_refs(
        self,
        *,
        run_id: str,
        tool_results: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split oversized tool summaries into artifact refs + bounded previews."""
        artifact_refs: list[dict[str, Any]] = []
        normalized_results: list[dict[str, Any]] = []
        for item in tool_results:
            payload = dict(item)
            summary = payload.get("summary")
            if isinstance(summary, str) and len(summary) > 512:
                preview, stored = split_preview_and_artifact(
                    content=summary,
                    max_preview_chars=512,
                )
                ref = self._deps.artifact_store.put(stored)
                self._deps.context_store.attach_artifact(run_id, ref)
                payload["summary"] = preview.text
                payload["summary_artifact_ref"] = ref.model_dump(mode="json")
                artifact_refs.append(ref.model_dump(mode="json"))
            normalized_results.append(payload)
        return normalized_results, artifact_refs

    def _sanitize_terminal_answer(self, context: RunContext) -> str | None:
        """Strip text-form tool call markup from the final assistant answer."""
        from agent_driver.runtime.single_agent.node_contract import (
            early_finalize_answer,
        )

        # Layer C: an early finalize from tool evidence synthesises the terminal
        # answer from envelopes with no model turn — prefer it when present.
        early = early_finalize_answer(context)
        if early is not None:
            return early
        if context.llm_response is None:
            return None
        raw = context.llm_response.message.content
        if not isinstance(raw, str):
            return raw
        if not raw.strip():
            return self._deep_research_artifact_handoff_answer(context, raw) or None
        cleaned = strip_text_form_tool_calls(raw)
        # Epic 043 A: a leading <think> block is CoT, not answer — it must not
        # reach the terminal answer surface (nor any host that persists it).
        cleaned, _think_stripped = strip_leading_think_block(cleaned)
        if cleaned != raw:
            get_streaming_runtime_state(context).set_raw_assistant_content(raw)
        answer = self._deep_research_artifact_handoff_answer(context, cleaned)
        if answer != cleaned:
            get_streaming_runtime_state(context).set_raw_assistant_content(raw)
        return answer or None

    def _deep_research_artifact_handoff_answer(
        self, context: RunContext, answer: str
    ) -> str:
        """Clamp completed Deep Research runs to an artifact handoff."""
        if not _deep_research_terminal_handoff_ready(context):
            return answer
        stripped = answer.strip()
        if _is_concise_deep_research_handoff(stripped):
            return answer
        get_streaming_runtime_state(context).set_raw_assistant_content(answer)
        return (
            "Deep Research report is ready at `research/report.md`. "
            "The source ledger is available at `research/sources.jsonl`."
        )

    def _source_evidence_from_tool_results(
        self, tool_results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Build a run-level source evidence list from normalized tool results."""
        records: list[dict[str, Any]] = []
        for item in tool_results:
            call = item.get("call")
            if not isinstance(call, dict):
                continue
            tool_name = call.get("tool_name")
            if not isinstance(tool_name, str):
                continue
            if item.get("error"):
                continue
            decision = str(item.get("decision") or "").lower()
            if decision in {"deny", "interrupt"}:
                continue
            tool_call_id = call.get("tool_call_id")
            structured = item.get("structured_output")
            records.extend(
                source_evidence_from_tool_result(
                    tool_name=tool_name,
                    structured_output=structured,
                    tool_call_id=(
                        tool_call_id if isinstance(tool_call_id, str) else None
                    ),
                )
            )
        return merge_source_evidence(records)

    def _build_output(
        self,
        context: RunContext,
        terminal: TerminalResult,
    ) -> AgentRunOutput:
        command_store = getattr(self._deps, "command_queue_store", None)
        commit_terminal = getattr(command_store, "commit_terminal", None)
        if callable(commit_terminal):
            changed = commit_terminal(
                context.run_id,
                stopped=terminal.reason
                in (
                    TerminalReason.CANCELLED_BY_USER,
                    TerminalReason.CANCELLATION_FAILED,
                ),
            )
            if changed:
                for item in changed:
                    self._emit(
                        EventSpec(
                            run_id=context.run_id,
                            attempt_id=context.attempt_id,
                            event_type=live_message_transition_event(item),
                            payload=live_message_receipt(item),
                        )
                    )
                context.metadata["live_message_terminal_reconciliation"] = [
                    {
                        "queue_id": item.queue_id,
                        "requested_semantic": (
                            item.requested_semantic.value
                            if item.requested_semantic is not None
                            else None
                        ),
                        "resolved_semantic": (
                            item.resolved_semantic.value
                            if item.resolved_semantic is not None
                            else None
                        ),
                        "reason_code": item.reason_code,
                    }
                    for item in changed
                ]
        answer = self._sanitize_terminal_answer(context)
        # Planning events must be emitted BEFORE the run-event snapshot below —
        # otherwise they land in the event log but fall outside ``run_events`` and
        # never reach ``output.events`` (regressed when epic 015 moved this snapshot
        # up to feed degenerate-answer recovery; the paused-output path already
        # emits before its snapshot).
        self._emit_planning_events(context)
        run_events = self._deps.event_log.list_for_run(context.run_id)
        # Epic 015 Phase C: recover a real answer discarded by a degenerate no-progress finalize
        # (empty or a short «already-answered» restatement on a tool-less over-iteration). No-op for
        # well-behaved runs; never overrides a tool-informed terminal answer (gated on 0 tool calls).
        recovered_answer, recovered_reason = recover_degenerate_terminal_answer(
            events=run_events,
            terminal_answer=answer,
            tool_call_count=context.tool_calls,
        )
        answer_recovered = recovered_answer is not None
        if answer_recovered:
            answer = recovered_answer
        usage = context.llm_response.usage if context.llm_response else None
        messages = [ChatMessage(role="assistant", content=answer)] if answer else []
        tool_trace = collect_tool_trace(context)
        normalized_tool_results, artifact_refs = self._metadata_with_artifact_refs(
            run_id=context.run_id,
            tool_results=list_dict_metadata(context, "tool_results"),
        )
        digest_refs = self._persist_session_artifacts(
            context=context, answer=answer, artifact_refs=artifact_refs
        )
        projection = build_memory_projection_for_context(
            context,
            answer=answer,
            normalized_tool_results=normalized_tool_results,
            artifact_refs=artifact_refs,
            digest_refs=digest_refs,
        )
        terminal_metadata = self._terminal_metadata(
            context,
            normalized_tool_results=normalized_tool_results,
            artifact_refs=artifact_refs,
            digest_refs=digest_refs,
        )
        if answer_recovered:
            terminal_metadata = {
                **terminal_metadata,
                "answer_recovered": True,
                "answer_recovered_reason": recovered_reason,
            }
        # Epic 036: run-level structured-output validation. Inert unless the caller
        # set a schema. A schema-valid JSON answer is stored as a typed terminal
        # artifact; anything else surfaces an error key (empty/invalid structured
        # final is a signal, never a silently-``completed`` run with junk).
        schema = getattr(context.run_input, "structured_output", None)
        if isinstance(schema, dict) and schema:
            terminal_metadata = {
                **terminal_metadata,
                **_validate_structured_terminal(answer, schema),
            }
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=terminal.status,
            answer=answer,
            messages=messages,
            events=run_events,
            tool_trace=tool_trace,
            usage=usage,
            interrupt=get_loop_control_state(context).interrupt_payload(),
            terminal_reason=terminal.reason,
            context=self._context_diagnostics(context),
            memory_projection=projection,
            memory_audit=build_memory_audit(context),
            metadata=terminal_metadata,
        )

    def _terminal_metadata(
        self,
        context: RunContext,
        *,
        normalized_tool_results: list[dict[str, Any]],
        artifact_refs: list[dict[str, Any]],
        digest_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        subagent_runs_raw = list_dict_metadata(context, "subagent_runs")
        source_evidence = self._source_evidence_from_tool_results(
            normalized_tool_results
        )
        source_ledger = research_source_ledger_from_tool_results(
            normalized_tool_results,
            assistant_text=(
                context.llm_response.message.content
                if context.llm_response is not None
                else ""
            ),
        ).model_dump()
        compaction_projection = get_compaction_runtime_state(
            context
        ).output_metadata_projection()
        planning_state = get_planning_runtime_state(context)
        metadata = {
            "graph_id": self.graph_id,
            "tool_results": normalized_tool_results,
            "source_evidence": source_evidence,
            "source_ledger": source_ledger,
            "artifact_refs": self._normalize_context_artifacts(
                context.run_id, artifact_refs
            ),
            "digest_refs": digest_refs,
            **compaction_projection,
            "subagent_groups": list_dict_metadata(context, "subagent_groups"),
            "subagent_runs": summarize_child_runs_for_parent(subagent_runs_raw),
            "approval_payload": self._approval_payload_from_context(context),
            "approved_plan": planning_state.approved_plan(),
            "step_count": context.step_count,
            "tool_calls": context.tool_calls,
            "raw_assistant_content": get_streaming_runtime_state(
                context
            ).raw_assistant_content(),
            # Epic 044 C: per-category context composition (the /context view), using
            # the same chars//4 heuristic as the compaction trigger so the host's
            # number and the trigger agree. Fail-open — never break finalize.
            "context_breakdown": _safe_context_breakdown(context),
        }
        # Epic 030 C: leftover-steer protocol — steering messages that arrived
        # after the last drain (still QUEUED at finalization) are handed back to
        # the host so it delivers them on the NEXT turn instead of dangling in the
        # queue. Raw-free: kind + a short text preview only.
        leftover = self._leftover_controls(context)
        if leftover:
            metadata["leftover_controls"] = leftover
        reconciliation = context.metadata.get("live_message_terminal_reconciliation")
        if isinstance(reconciliation, list):
            metadata["live_message_terminal_reconciliation"] = reconciliation
        if context.llm_response is not None:
            output_audio = context.llm_response.message.metadata.get("output_audio")
            if isinstance(output_audio, dict):
                metadata["output_audio"] = output_audio
        research_artifacts = context.metadata.get("deep_research_artifacts")
        if isinstance(research_artifacts, dict):
            metadata["deep_research_artifacts"] = dict(research_artifacts)
        child_synthesis = context.metadata.get("deep_research_child_synthesis")
        if isinstance(child_synthesis, dict):
            metadata["deep_research_child_synthesis"] = dict(child_synthesis)
        research_contract = context.metadata.get("research_session_contract")
        if isinstance(research_contract, dict):
            metadata["research_session_contract"] = dict(research_contract)
        from agent_driver.runtime.single_agent.node_contract import output_summary

        node_contract_summary = output_summary(context)
        if node_contract_summary is not None:
            metadata["node_contract"] = node_contract_summary
        return metadata

    def _leftover_controls(self, context: RunContext) -> list[dict[str, Any]]:
        """Raw-free summary of steering messages still QUEUED at finalization.

        Epic 030 C: NEXT/LATER items that arrived after the last drain would
        otherwise dangle in the store until the next run. Surfaced here so the
        host re-delivers them on the next turn. ENQUEUE/REDIRECT kinds only (the
        message-carrying ones); a short preview, never the full text.
        """
        store = getattr(self._deps, "command_queue_store", None)
        if store is None:
            return []
        try:
            pending = store.list_pending()
        except Exception:  # noqa: BLE001 - store read must never break finalize
            return []
        out: list[dict[str, Any]] = []
        for item in pending:
            if not _control_matches_run(item, context):
                continue
            kind = str(getattr(item.kind, "value", item.kind))
            if kind not in ("enqueue_user_message", "redirect_user_message"):
                continue
            out.append(
                {
                    "queue_id": item.queue_id,
                    "kind": kind,
                    "requested_semantic": (
                        item.requested_semantic.value
                        if item.requested_semantic is not None
                        else None
                    ),
                    "resolved_semantic": (
                        item.resolved_semantic.value
                        if item.resolved_semantic is not None
                        else None
                    ),
                    "applies_at": item.applies_at,
                    "reason_code": item.reason_code,
                    "content_sha256": item.content_sha256,
                }
            )
        return out

    def _approval_payload_from_context(
        self, context: RunContext
    ) -> dict[str, Any] | None:
        interrupt_payload = get_loop_control_state(context).interrupt_payload()
        if not isinstance(interrupt_payload, dict):
            return None
        return ApprovalPayload.from_interrupt(
            InterruptRequest.model_validate(interrupt_payload)
        ).model_dump(mode="json")

    def _build_paused_output(self, context: RunContext, result: Any) -> AgentRunOutput:
        """Build paused output envelope for pending interrupt."""
        self._emit_planning_events(context)
        artifact_refs = list_dict_metadata(context, "artifact_refs")
        digest_refs = list_dict_metadata(context, "digest_refs")
        tool_results = list_dict_metadata(context, "tool_results")
        source_evidence = self._source_evidence_from_tool_results(tool_results)
        source_ledger = research_source_ledger_from_tool_results(
            tool_results,
        ).model_dump()
        projection = build_memory_projection_for_context(
            context,
            answer=None,
            normalized_tool_results=tool_results,
            artifact_refs=artifact_refs,
            digest_refs=digest_refs,
        )
        compaction_projection = get_compaction_runtime_state(
            context
        ).output_metadata_projection()
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=RunStatus.PAUSED,
            events=self._deps.event_log.list_for_run(context.run_id),
            tool_trace=result.traces,
            interrupt=result.interrupt,
            context=self._context_diagnostics(context),
            memory_projection=projection,
            memory_audit=build_memory_audit(context),
            subagent_groups=list_dict_metadata(context, "subagent_groups"),
            subagent_runs=list_dict_metadata(context, "subagent_runs"),
            metadata={
                "graph_id": self.graph_id,
                "tool_results": tool_results,
                "source_evidence": source_evidence,
                "source_ledger": source_ledger,
                "artifact_refs": self._normalize_context_artifacts(
                    context.run_id, artifact_refs
                ),
                "digest_refs": digest_refs,
                **compaction_projection,
                "subagent_groups": list_dict_metadata(context, "subagent_groups"),
                "subagent_runs": list_dict_metadata(context, "subagent_runs"),
                "approval_payload": ApprovalPayload.from_interrupt(
                    result.interrupt
                ).model_dump(mode="json"),
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            },
        )

    def _context_diagnostics(self, context: RunContext) -> ContextDiagnostics:
        token_pressure = get_compaction_runtime_state(context).token_pressure()
        state = str(token_pressure.get("state", "ok")) if token_pressure else "ok"
        recommendation = {
            "early_warning": "summarize_findings",
            "delegate_or_summarize": "delegate_or_summarize",
            "warning": "summarize_findings",
            "compact_recommended": "compact_recommended",
            "blocking": "blocking",
        }.get(state, "continue")
        return ContextDiagnostics(
            pressure=state,
            recommendation=recommendation,
            token_pressure=token_pressure,
        )


def _validate_structured_terminal(answer: str, schema: dict) -> dict:
    """Validate a terminal answer as a schema-conformant JSON object (epic 036).

    Returns terminal-metadata keys: ``structured_output`` (the parsed object) on
    success, else ``structured_output_error`` describing why. Reuses the primitive's
    lightweight validator so run-final and aux-call validation agree.
    """
    import json

    from agent_driver.llm.structured import _validate

    text = (answer or "").strip()
    if not text:
        return {"structured_output_error": "empty terminal answer"}
    # Tolerate a fenced/embedded object the same way parse-ladders do.
    candidate = text
    if not candidate.startswith("{"):
        import re

        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        candidate = match.group(0) if match else candidate
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return {"structured_output_error": "terminal answer is not JSON"}
    if not isinstance(parsed, dict):
        return {"structured_output_error": "terminal answer is not a JSON object"}
    violations = _validate(parsed, schema)
    if violations:
        return {"structured_output_error": "; ".join(violations)}
    return {"structured_output": parsed}


__all__ = ["SingleAgentOutputMixin"]
