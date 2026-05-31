"""Build AgentRunOutput for terminal and paused states."""

from __future__ import annotations

from typing import Any

from agent_driver.context import (
    planning_state_event,
    planning_step_event,
    split_preview_and_artifact,
)
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
from agent_driver.contracts.enums import RunStatus
from agent_driver.contracts.interrupts import ApprovalPayload, InterruptRequest
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunOutput, ContextDiagnostics
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
from agent_driver.runtime.research_evidence import (
    research_source_ledger_from_tool_results,
)
from agent_driver.runtime.single_agent.finalization.output_builders import (
    build_memory_audit,
    build_memory_projection_for_context,
    collect_tool_trace,
    list_dict_metadata,
)
from agent_driver.runtime.single_agent.types import (
    RunContext,
    RunnerDeps,
    TerminalResult,
)
from agent_driver.subagents import summarize_child_runs_for_parent


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
        if context.llm_response is None:
            return None
        raw = context.llm_response.message.content
        if not isinstance(raw, str) or not raw.strip():
            return raw
        cleaned = strip_text_form_tool_calls(raw)
        if cleaned != raw:
            get_streaming_runtime_state(context).set_raw_assistant_content(raw)
        return cleaned or None

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
        answer = self._sanitize_terminal_answer(context)
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
        self._emit_planning_events(context)
        projection = build_memory_projection_for_context(
            context,
            answer=answer,
            normalized_tool_results=normalized_tool_results,
            artifact_refs=artifact_refs,
            digest_refs=digest_refs,
        )
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=terminal.status,
            answer=answer,
            messages=messages,
            events=self._deps.event_log.list_for_run(context.run_id),
            tool_trace=tool_trace,
            usage=usage,
            interrupt=get_loop_control_state(context).interrupt_payload(),
            terminal_reason=terminal.reason,
            context=self._context_diagnostics(context),
            memory_projection=projection,
            memory_audit=build_memory_audit(context),
            metadata=self._terminal_metadata(
                context,
                normalized_tool_results=normalized_tool_results,
                artifact_refs=artifact_refs,
                digest_refs=digest_refs,
            ),
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
            assistant_text=context.llm_response.message.content
            if context.llm_response is not None
            else "",
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
        }
        research_artifacts = context.metadata.get("deep_research_artifacts")
        if isinstance(research_artifacts, dict):
            metadata["deep_research_artifacts"] = dict(research_artifacts)
        return metadata

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


__all__ = ["SingleAgentOutputMixin"]
