"""Compaction orchestration before LLM completion."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
    CompactionOrchestrator,
    apply_post_compact_cleanup,
    build_partial_compaction,
    build_session_memory_compaction,
    evaluate_session_memory_freshness,
    load_session_memory,
    ptl_retry_drop_oldest_groups,
    run_full_llm_compaction,
    sanitize_compaction_text,
)
from agent_driver.contracts import CompactionDecision
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
)


class CompactionStageHost(Protocol):
    """Host surface required for compaction stage helpers."""

    _deps: RunnerDeps
    _config: RunnerConfig
    _compaction_orchestrator: CompactionOrchestrator | None

    def _get_compaction_orchestrator(self) -> CompactionOrchestrator: ...
    def _emit(self, event: EventSpec) -> None: ...


def _emit_compaction_outcome(
    host: CompactionStageHost,
    *,
    context: RunContext,
    outcome: str,
    payload_extras: dict[str, Any],
    orchestrator: CompactionOrchestrator,
) -> None:
    """Emit MEMORY_COMPACTED with a stable outcome tag and orchestrator state.

    `outcome` is one of: ``"skipped"``, ``"successful"``, ``"failed"``. Hosts
    use this field to bucket runtime metrics (skipped/successful/failed
    counters) without parsing the union of historical payload shapes. The
    orchestrator state is forwarded so a host can detect circuit-breaker
    transitions without keeping its own copy of the counters.
    """
    payload: dict[str, Any] = {"outcome": outcome}
    payload.update(payload_extras)
    payload["compaction_state"] = orchestrator.state_snapshot()
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=RuntimeEventType.MEMORY_COMPACTED,
            payload=payload,
        )
    )


def _maybe_emit_circuit_breaker_warning(
    host: CompactionStageHost,
    *,
    context: RunContext,
    before_open: bool,
    orchestrator: CompactionOrchestrator,
) -> None:
    """Emit a WARNING when consecutive_failures crossed failure_limit on this attempt.

    The event uses ``kind="compaction_circuit_breaker"`` so it projects
    through the existing :func:`agent_driver.adapters.project_warning_event`
    helper alongside ``token_pressure`` and ``tool_choice_antipattern``
    warnings, keeping one stable warning vocabulary for SSE consumers.
    """
    state = orchestrator.state_snapshot()
    after_open = bool(state.get("circuit_breaker_open"))
    if after_open and not before_open:
        host._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.WARNING,
                payload={
                    "kind": "compaction_circuit_breaker",
                    "signal_id": "compaction_circuit_breaker_open",
                    "severity": "critical",
                    "description": (
                        "compaction circuit breaker opened: "
                        f"{state.get('consecutive_failures')} consecutive failures "
                        f"reached the configured limit of "
                        f"{state.get('failure_limit')}"
                    ),
                    "consecutive_failures": state.get("consecutive_failures"),
                    "failure_limit": state.get("failure_limit"),
                },
            )
        )


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
        context.metadata[COMPACTION_FAILURES_KEY] = []
        context.metadata[COMPACTION_AUDIT_KEY] = {
            "decision": context.metadata[COMPACTION_DECISION_KEY]
        }
        skip_payload: dict[str, Any] = {
            "mode": decision.mode.value,
        }
        if decision.skip_reason is not None:
            skip_payload["skip_reason"] = decision.skip_reason.value
        _emit_compaction_outcome(
            host,
            context=context,
            outcome="skipped",
            payload_extras=skip_payload,
            orchestrator=orchestrator,
        )
        return
    circuit_breaker_open_before = bool(
        orchestrator.state_snapshot().get("circuit_breaker_open")
    )
    compaction_id = orchestrator.start_attempt()
    context.metadata["active_compaction_id"] = compaction_id
    attempted_llm_full = False
    if decision.mode.value == "session_memory" and session_memory is not None:
        if await _apply_session_memory_compaction(
            host,
            context=context,
            request=request,
            session_memory=session_memory,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        ):
            return
        if host._config.enable_llm_compaction:
            attempted_llm_full = True
            if await _apply_llm_full_compaction(
                host,
                context=context,
                request=request,
                orchestrator=orchestrator,
                decision=decision,
                compaction_id=compaction_id,
                circuit_breaker_open_before=circuit_breaker_open_before,
            ):
                return
    if decision.mode.value == "llm_full":
        attempted_llm_full = True
        if await _apply_llm_full_compaction(
            host,
            context=context,
            request=request,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        ):
            return
    if host._config.enable_partial_compaction and (
        decision.mode.value == "partial"
        or (decision.mode.value != "partial" and not attempted_llm_full)
    ):
        if await _apply_partial_compaction(
            host,
            context=context,
            request=request,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        ):
            return
    failure = {
        "kind": "path_not_implemented",
        "mode": decision.mode.value,
        "message": "compaction path not implemented",
    }
    audit = orchestrator.complete_attempt(
        decision=decision,
        failures=[failure],
    )
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    context.metadata[COMPACTION_RESULT_KEY] = None
    context.metadata[COMPACTION_FAILURES_KEY] = [failure]
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="failed",
        payload_extras={
            "mode": decision.mode.value,
            "compaction_id": compaction_id,
            "failure_kind": failure["kind"],
            "failure_message": failure["message"],
        },
        orchestrator=orchestrator,
    )
    _maybe_emit_circuit_breaker_warning(
        host,
        context=context,
        before_open=circuit_breaker_open_before,
        orchestrator=orchestrator,
    )


async def _apply_session_memory_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    session_memory: Any,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
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
        "compaction_id": compaction_id,
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
    cleanup = apply_post_compact_cleanup(
        metadata=context.metadata,
        max_reinjected_artifact_refs=host._config.post_compact_max_reinjected_artifact_refs,
    )
    context.metadata["post_compact_cleanup"] = {
        "cleaned_keys": list(cleanup.cleaned_keys),
        "reinjected_keys": list(cleanup.reinjected_keys),
    }
    context.metadata[COMPACTION_FAILURES_KEY] = []
    audit = orchestrator.complete_attempt(
        decision=decision,
        result=_result_from_payload(result_payload),
    )
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="successful",
        payload_extras={
            "mode": "session_memory",
            "compaction_id": compaction_id,
            "retained_digest_ids": compacted.retained_digest_ids,
            "retained_artifact_ids": compacted.retained_artifact_ids,
        },
        orchestrator=orchestrator,
    )
    _maybe_emit_circuit_breaker_warning(
        host,
        context=context,
        before_open=circuit_breaker_open_before,
        orchestrator=orchestrator,
    )
    return True


async def _apply_llm_full_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    raw_groups = [str(message.content) for message in request.messages[-8:]]
    kept_groups = list(raw_groups)
    dropped_groups: list[str] = []
    if host._config.enable_ptl_retry:
        kept_groups, dropped_groups = ptl_retry_drop_oldest_groups(
            groups=raw_groups,
            max_chars=host._config.ptl_retry_max_chars,
        )
    history_excerpt = "\n".join(kept_groups)
    sanitized_excerpt = sanitize_compaction_text(history_excerpt)
    compaction_result, summary = await run_full_llm_compaction(
        provider=host._deps.provider,
        model=host._config.compaction_model,
        history_excerpt=sanitized_excerpt,
        user_request=context.run_input.input or "",
    )
    if compaction_result is None or not compaction_result.success:
        failure = {
            "kind": "llm_compaction_failed",
            "mode": "llm_full",
            "message": "provider compaction returned unsuccessful result",
        }
        audit = orchestrator.complete_attempt(
            decision=decision,
            result=compaction_result,
            failures=[failure],
        )
        context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
        context.metadata[COMPACTION_RESULT_KEY] = (
            compaction_result.model_dump(mode="json")
            if compaction_result is not None
            else None
        )
        context.metadata[COMPACTION_FAILURES_KEY] = [failure]
        _emit_compaction_outcome(
            host,
            context=context,
            outcome="failed",
            payload_extras={
                "mode": "llm_full",
                "compaction_id": compaction_id,
                "failure_kind": failure["kind"],
                "failure_message": failure["message"],
            },
            orchestrator=orchestrator,
        )
        _maybe_emit_circuit_breaker_warning(
            host,
            context=context,
            before_open=circuit_breaker_open_before,
            orchestrator=orchestrator,
        )
        return True
    compaction_result = compaction_result.model_copy(
        update={"compaction_id": compaction_id}
    )
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
    if isinstance(context.metadata[COMPACTION_RESULT_KEY], dict):
        context.metadata[COMPACTION_RESULT_KEY]["metadata"] = {
            **context.metadata[COMPACTION_RESULT_KEY].get("metadata", {}),
            "ptl_retry": {
                "enabled": host._config.enable_ptl_retry,
                "dropped_groups": len(dropped_groups),
                "kept_groups": len(kept_groups),
                "max_chars": host._config.ptl_retry_max_chars,
            },
        }
    cleanup = apply_post_compact_cleanup(
        metadata=context.metadata,
        max_reinjected_artifact_refs=host._config.post_compact_max_reinjected_artifact_refs,
    )
    context.metadata["post_compact_cleanup"] = {
        "cleaned_keys": list(cleanup.cleaned_keys),
        "reinjected_keys": list(cleanup.reinjected_keys),
    }
    context.metadata[COMPACTION_FAILURES_KEY] = []
    audit = orchestrator.complete_attempt(
        decision=decision,
        result=compaction_result,
    )
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="successful",
        payload_extras={
            "mode": "llm_full",
            "compaction_id": compaction_id,
            "model": compaction_result.model,
            "latency_ms": compaction_result.latency_ms,
            "input_tokens_estimate": compaction_result.input_tokens_estimate,
            "output_tokens_estimate": compaction_result.output_tokens_estimate,
        },
        orchestrator=orchestrator,
    )
    _maybe_emit_circuit_breaker_warning(
        host,
        context=context,
        before_open=circuit_breaker_open_before,
        orchestrator=orchestrator,
    )
    return True


async def _apply_partial_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    compacted = build_partial_compaction(
        messages=[msg.model_dump(mode="json") for msg in request.messages],
        retain_recent_messages=6,
        prefix_mode=True,
    )
    request.messages = [
        ChatMessage.model_validate(item) for item in compacted.prompt_messages
    ]
    result_payload = {
        "compaction_id": compaction_id,
        "mode": "partial",
        "success": True,
        "retained_observation_ids": compacted.retained_observation_ids,
        "metadata": compacted.metadata,
    }
    context.metadata[COMPACTION_RESULT_KEY] = result_payload
    context.metadata[COMPACTION_FAILURES_KEY] = []
    cleanup = apply_post_compact_cleanup(
        metadata=context.metadata,
        max_reinjected_artifact_refs=host._config.post_compact_max_reinjected_artifact_refs,
    )
    context.metadata["post_compact_cleanup"] = {
        "cleaned_keys": list(cleanup.cleaned_keys),
        "reinjected_keys": list(cleanup.reinjected_keys),
    }
    audit = orchestrator.complete_attempt(
        decision=decision,
        result=_result_from_payload(result_payload),
    )
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="successful",
        payload_extras={
            "mode": "partial",
            "compaction_id": compaction_id,
            "summarized_message_count": compacted.metadata.get(
                "summarized_message_count"
            ),
        },
        orchestrator=orchestrator,
    )
    _maybe_emit_circuit_breaker_warning(
        host,
        context=context,
        before_open=circuit_breaker_open_before,
        orchestrator=orchestrator,
    )
    return True


def _result_from_payload(payload: dict[str, Any]):
    from agent_driver.contracts import CompactionMode, CompactionResult

    mode_raw = str(payload.get("mode", "none"))
    try:
        mode = CompactionMode(mode_raw)
    except ValueError:
        mode = CompactionMode.NONE
    return CompactionResult(
        compaction_id=str(payload.get("compaction_id", "cmp_unknown")),
        mode=mode,
        success=bool(payload.get("success", False)),
        metadata=(
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        ),
        retained_digest_ids=[
            str(item) for item in payload.get("retained_digest_ids", []) if item
        ],
        retained_artifact_ids=[
            str(item) for item in payload.get("retained_artifact_ids", []) if item
        ],
    )


__all__ = ["CompactionStageHost", "apply_compaction_if_eligible"]
