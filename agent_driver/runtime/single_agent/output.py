"""Build AgentRunOutput for terminal and paused states."""

from __future__ import annotations

from typing import Any

from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
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
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.runtime.single_agent.output_builders import (
    build_memory_audit,
    build_memory_projection_for_context,
    collect_tool_trace,
    dict_metadata,
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

    def _maybe_update_session_memory(self, *, context: RunContext, session_id: str) -> None:
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
        context.metadata["session_memory_extraction"] = {
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

    def _emit_planning_events(self, context: RunContext) -> None:
        """Emit dedicated planning events if state exists in metadata."""
        step_payload = context.metadata.get("planning_step")
        if isinstance(step_payload, dict):
            self._deps.event_log.append(
                planning_step_event(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    seq=self._next_seq(context.run_id),
                    step=PlanningStep.model_validate(step_payload),
                )
            )
        state_payload = context.metadata.get("planning_state")
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

    def _build_output(
        self,
        context: RunContext,
        terminal: TerminalResult,
    ) -> AgentRunOutput:
        answer = context.llm_response.message.content if context.llm_response else None
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
            interrupt=context.metadata.get("interrupt_payload"),
            terminal_reason=terminal.reason,
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
        return {
            "graph_id": self.graph_id,
            "tool_results": normalized_tool_results,
            "artifact_refs": self._normalize_context_artifacts(
                context.run_id, artifact_refs
            ),
            "digest_refs": digest_refs,
            "observations": context.metadata.get("observations", []),
            "trim_audit": context.metadata.get("trim_audit", []),
            "trim_metadata": context.metadata.get("trim_metadata", {}),
            "microcompaction_audit": context.metadata.get("microcompaction_audit", []),
            "microcompaction": context.metadata.get("microcompaction", {}),
            "token_pressure": context.metadata.get("token_pressure", {}),
            "subagent_groups": list_dict_metadata(context, "subagent_groups"),
            "subagent_runs": summarize_child_runs_for_parent(subagent_runs_raw),
            COMPACTION_DECISION_KEY: context.metadata.get(COMPACTION_DECISION_KEY),
            COMPACTION_AUDIT_KEY: context.metadata.get(COMPACTION_AUDIT_KEY),
            COMPACTION_RESULT_KEY: context.metadata.get(COMPACTION_RESULT_KEY),
            COMPACTION_FAILURES_KEY: context.metadata.get(COMPACTION_FAILURES_KEY, []),
            "post_compact_cleanup": context.metadata.get("post_compact_cleanup", {}),
            "session_memory_extraction": context.metadata.get(
                "session_memory_extraction", {}
            ),
            "prompt_render": context.metadata.get("prompt_render"),
            "approval_payload": self._approval_payload_from_context(context),
            "step_count": context.step_count,
            "tool_calls": context.tool_calls,
        }

    def _approval_payload_from_context(
        self, context: RunContext
    ) -> dict[str, Any] | None:
        interrupt_payload = context.metadata.get("interrupt_payload")
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
        projection = build_memory_projection_for_context(
            context,
            answer=None,
            normalized_tool_results=tool_results,
            artifact_refs=artifact_refs,
            digest_refs=digest_refs,
        )
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=RunStatus.PAUSED,
            events=self._deps.event_log.list_for_run(context.run_id),
            tool_trace=result.traces,
            interrupt=result.interrupt,
            memory_projection=projection,
            memory_audit=build_memory_audit(context),
            subagent_groups=list_dict_metadata(context, "subagent_groups"),
            subagent_runs=list_dict_metadata(context, "subagent_runs"),
            metadata={
                "graph_id": self.graph_id,
                "tool_results": tool_results,
                "artifact_refs": self._normalize_context_artifacts(
                    context.run_id, artifact_refs
                ),
                "digest_refs": digest_refs,
                "observations": context.metadata.get("observations", []),
                "trim_audit": context.metadata.get("trim_audit", []),
                "trim_metadata": context.metadata.get("trim_metadata", {}),
                "microcompaction_audit": context.metadata.get(
                    "microcompaction_audit", []
                ),
                "microcompaction": context.metadata.get("microcompaction", {}),
                "token_pressure": context.metadata.get("token_pressure", {}),
                "subagent_groups": list_dict_metadata(context, "subagent_groups"),
                "subagent_runs": list_dict_metadata(context, "subagent_runs"),
                "post_compact_cleanup": context.metadata.get(
                    "post_compact_cleanup", {}
                ),
                "session_memory_extraction": context.metadata.get(
                    "session_memory_extraction", {}
                ),
                "prompt_render": context.metadata.get("prompt_render"),
                "approval_payload": ApprovalPayload.from_interrupt(
                    result.interrupt
                ).model_dump(mode="json"),
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            },
        )


__all__ = ["SingleAgentOutputMixin"]
