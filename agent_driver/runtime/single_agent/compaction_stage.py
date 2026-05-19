"""Compaction orchestration before LLM completion."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
    CompactionOrchestrator,
    build_session_memory_compaction,
    evaluate_session_memory_freshness,
    load_session_memory,
    run_full_llm_compaction,
    sanitize_compaction_text,
)
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.types import EventSpec, RunContext, RunnerConfig, RunnerDeps


class CompactionStageHost(Protocol):
    """Host surface required for compaction stage helpers."""

    _deps: RunnerDeps
    _config: RunnerConfig
    _compaction_orchestrator: CompactionOrchestrator | None

    def _get_compaction_orchestrator(self) -> CompactionOrchestrator: ...
    def _emit(self, event: EventSpec) -> None: ...


async def apply_compaction_if_eligible(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    token_pressure_state: str,
) -> None:
    """Run compaction orchestration before final provider completion."""
    orchestrator = host._get_compaction_orchestrator()
    session_memory = load_session_memory(
        artifact_store=host._deps.artifact_store,
        session_id=context.run_input.thread_id or context.run_id,
    )
    decision = orchestrator.decide(
        enable_compaction=host._config.enable_compaction,
        enable_session_memory_compaction=host._config.enable_session_memory_compaction,
        enable_llm_compaction=host._config.enable_llm_compaction,
        token_pressure_state=token_pressure_state,
        session_memory=session_memory,
    )
    context.metadata[COMPACTION_DECISION_KEY] = decision.model_dump(mode="json")
    if not decision.eligible:
        context.metadata[COMPACTION_AUDIT_KEY] = {
            "decision": context.metadata[COMPACTION_DECISION_KEY]
        }
        return
    if decision.mode.value == "session_memory" and session_memory is not None:
        if await _apply_session_memory_compaction(
            host, context=context, request=request, session_memory=session_memory, orchestrator=orchestrator
        ):
            return
    if decision.mode.value == "llm_full":
        if await _apply_llm_full_compaction(
            host, context=context, request=request, orchestrator=orchestrator
        ):
            return
    placeholder = orchestrator.execute_placeholder(decision)
    context.metadata[COMPACTION_AUDIT_KEY] = placeholder.model_dump(mode="json")
    context.metadata[COMPACTION_RESULT_KEY] = (
        placeholder.result.model_dump(mode="json") if placeholder.result else None
    )
    context.metadata[COMPACTION_FAILURES_KEY] = placeholder.failures


async def _apply_session_memory_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    session_memory: Any,
    orchestrator: CompactionOrchestrator,
) -> bool:
    freshness = evaluate_session_memory_freshness(
        session_memory=session_memory,
        latest_turn_index=int(context.metadata.get("step_count", 0)),
        stale_after_turns=host._config.session_memory_stale_after_turns,
    )
    if freshness.state != "fresh":
        return False
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
    host._emit(
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
    return True


async def _apply_llm_full_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    orchestrator: CompactionOrchestrator,
) -> bool:
    history_excerpt = "\n".join(message.content for message in request.messages[-8:])
    sanitized_excerpt = sanitize_compaction_text(history_excerpt)
    compaction_result, summary = await run_full_llm_compaction(
        provider=host._deps.provider,
        model=host._config.compaction_model,
        history_excerpt=sanitized_excerpt,
        user_request=context.run_input.input or "",
    )
    if compaction_result is None or not compaction_result.success:
        return False
    request.messages = request.messages[-4:]
    summary_text = str(summary.get("current_work", ""))
    request.messages.append(
        ChatMessage.model_validate(
            {"role": "system", "content": f"Compacted summary:\n{summary_text}"}
        )
    )
    context.metadata[COMPACTION_RESULT_KEY] = compaction_result.model_dump(mode="json")
    context.metadata[COMPACTION_AUDIT_KEY] = {
        "decision": context.metadata[COMPACTION_DECISION_KEY],
        "result": context.metadata[COMPACTION_RESULT_KEY],
    }
    host._emit(
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
    return True


__all__ = ["CompactionStageHost", "apply_compaction_if_eligible"]
