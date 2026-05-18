"""Build AgentRunOutput for terminal and paused states."""

from __future__ import annotations

from typing import Any

from agent_driver.context import (
    build_memory_projection,
    planning_state_event,
    planning_step_event,
    split_preview_and_artifact,
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
from agent_driver.contracts.tools import ToolTrace
from agent_driver.runtime.single_agent.types import (
    RunContext,
    RunnerDeps,
    TerminalResult,
)


class SingleAgentOutputMixin:  # pylint: disable=too-few-public-methods
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
        return [digest_ref]

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

    def _build_output(  # pylint: disable=too-many-locals
        self,
        context: RunContext,
        terminal: TerminalResult,
    ) -> AgentRunOutput:
        answer = context.llm_response.message.content if context.llm_response else None
        usage = context.llm_response.usage if context.llm_response else None
        messages = [ChatMessage(role="assistant", content=answer)] if answer else []
        tool_trace_payload = context.metadata.get("tool_trace", [])
        tool_trace = []
        if isinstance(tool_trace_payload, list):
            tool_trace = [
                ToolTrace.model_validate(item)
                for item in tool_trace_payload
                if isinstance(item, dict)
            ]
        tool_results_payload = context.metadata.get("tool_results", [])
        if not isinstance(tool_results_payload, list):
            tool_results_payload = []
        normalized_tool_results, artifact_refs = self._metadata_with_artifact_refs(
            run_id=context.run_id,
            tool_results=[
                item for item in tool_results_payload if isinstance(item, dict)
            ],
        )
        digest_refs = self._persist_session_artifacts(
            context=context, answer=answer, artifact_refs=artifact_refs
        )
        self._emit_planning_events(context)
        observations_payload = context.metadata.get("observations", [])
        observations = (
            [item for item in observations_payload if isinstance(item, dict)]
            if isinstance(observations_payload, list)
            else []
        )
        trim_metadata = context.metadata.get("trim_metadata", {})
        projection = build_memory_projection(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            answer=answer,
            observations=observations,
            planning_state=(
                context.metadata.get("planning_state")
                if isinstance(context.metadata.get("planning_state"), dict)
                else None
            ),
            trim_metadata=trim_metadata if isinstance(trim_metadata, dict) else {},
            artifact_refs=[item for item in artifact_refs if isinstance(item, dict)],
            digest_refs=[item for item in digest_refs if isinstance(item, dict)],
            prompt_render=(
                context.metadata.get("prompt_render")
                if isinstance(context.metadata.get("prompt_render"), dict)
                else None
            ),
            tool_results=normalized_tool_results,
        )
        memory_audit = {
            "trim_audit": context.metadata.get("trim_audit", []),
            "microcompaction_audit": context.metadata.get("microcompaction_audit", []),
            "token_pressure": context.metadata.get("token_pressure", {}),
            "retained_digest_ids": context.metadata.get("retained_digest_ids", []),
            "retained_artifact_ids": context.metadata.get("retained_artifact_ids", []),
        }
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
            memory_audit=memory_audit,
            metadata={
                "graph_id": self.graph_id,
                "tool_results": normalized_tool_results,
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
                "prompt_render": context.metadata.get("prompt_render"),
                "approval_payload": (
                    ApprovalPayload.from_interrupt(
                        InterruptRequest.model_validate(
                            context.metadata["interrupt_payload"]
                        )
                    ).model_dump(mode="json")
                    if isinstance(context.metadata.get("interrupt_payload"), dict)
                    else None
                ),
            },
        )

    def _build_paused_output(self, context: RunContext, result: Any) -> AgentRunOutput:
        """Build paused output envelope for pending interrupt."""
        self._emit_planning_events(context)
        artifact_refs = context.metadata.get("artifact_refs", [])
        if not isinstance(artifact_refs, list):
            artifact_refs = []
        digest_refs = context.metadata.get("digest_refs", [])
        if not isinstance(digest_refs, list):
            digest_refs = []
        observations_payload = context.metadata.get("observations", [])
        observations = (
            [item for item in observations_payload if isinstance(item, dict)]
            if isinstance(observations_payload, list)
            else []
        )
        trim_metadata = context.metadata.get("trim_metadata", {})
        projection = build_memory_projection(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            answer=None,
            observations=observations,
            planning_state=(
                context.metadata.get("planning_state")
                if isinstance(context.metadata.get("planning_state"), dict)
                else None
            ),
            trim_metadata=trim_metadata if isinstance(trim_metadata, dict) else {},
            artifact_refs=[item for item in artifact_refs if isinstance(item, dict)],
            digest_refs=[item for item in digest_refs if isinstance(item, dict)],
            prompt_render=(
                context.metadata.get("prompt_render")
                if isinstance(context.metadata.get("prompt_render"), dict)
                else None
            ),
            tool_results=(
                context.metadata.get("tool_results", [])
                if isinstance(context.metadata.get("tool_results"), list)
                else []
            ),
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
            memory_audit={
                "trim_audit": context.metadata.get("trim_audit", []),
                "microcompaction_audit": context.metadata.get(
                    "microcompaction_audit", []
                ),
                "token_pressure": context.metadata.get("token_pressure", {}),
            },
            metadata={
                "graph_id": self.graph_id,
                "tool_results": context.metadata.get("tool_results", []),
                "artifact_refs": self._normalize_context_artifacts(
                    context.run_id,
                    [item for item in artifact_refs if isinstance(item, dict)],
                ),
                "digest_refs": [item for item in digest_refs if isinstance(item, dict)],
                "observations": context.metadata.get("observations", []),
                "trim_audit": context.metadata.get("trim_audit", []),
                "trim_metadata": context.metadata.get("trim_metadata", {}),
                "microcompaction_audit": context.metadata.get(
                    "microcompaction_audit", []
                ),
                "microcompaction": context.metadata.get("microcompaction", {}),
                "token_pressure": context.metadata.get("token_pressure", {}),
                "prompt_render": context.metadata.get("prompt_render"),
                "approval_payload": ApprovalPayload.from_interrupt(
                    result.interrupt
                ).model_dump(mode="json"),
            },
        )


__all__ = ["SingleAgentOutputMixin"]
