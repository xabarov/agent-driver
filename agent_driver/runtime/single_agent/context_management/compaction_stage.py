"""Compaction orchestration before LLM completion."""

from __future__ import annotations

import hashlib
import json
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
    run_full_llm_compaction,
)
from agent_driver.contracts import CompactionDecision
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.context.compaction.condenser import (
    CondenseContext,
    CondenserPipeline,
    message_chars,
)
from agent_driver.context.compaction.condenser_tiers import default_condenser_tiers
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
)
from agent_driver.runtime.single_agent.context_management.compaction_helpers import (  # noqa: F401
    _message_material_unit_hashes,
    _material_unit_receipt,
    _is_protected_message,
    _retained_messages_after_full_compaction,
    _scaled_context_char_cap,
    _account_compaction_cost,
    _has_sendable_content,
    _pipeline_target_chars,
    _effective_window_tokens,
    _applied_tier_names,
    _FullCompactionExcerpt,
    _build_full_compaction_excerpt,
    _resolve_compaction_backend,
    _splice_summary_message,
    _result_from_payload,
    _COMPACTION_CHARS_PER_TOKEN,
    _MAX_SCALED_COMPACTION_CHARS,
)


def _apply_tool_arg_truncation(host: Any, *, context: RunContext, request: Any) -> None:
    """E5 pre-pass: clip oversized tool-call args in older messages in place.

    Cheap and LLM-free; runs whenever compaction is considered (token pressure)
    so it can shrink the next provider call's tokens before — or instead of —
    the expensive summarization. Records what it clipped under
    ``tool_arg_truncation``.
    """
    from agent_driver.context.tool_arg_truncation import (  # pylint: disable=import-outside-toplevel
        truncate_tool_call_args,
    )

    effective_max_chars, budget_source = _scaled_context_char_cap(
        host,
        context=context,
        base_max_chars=host._config.tool_arg_truncation_max_chars,
    )
    result = truncate_tool_call_args(
        list(request.messages),
        max_arg_chars=effective_max_chars,
    )
    if not result.changed and not result.retained_structured:
        return
    if result.changed:
        request.messages = result.messages
    context.metadata["tool_arg_truncation"] = {
        "chars_saved": result.chars_saved,
        "effective_max_chars": effective_max_chars,
        "budget_source": budget_source,
        "clipped": result.audit,
        "retained_structured": result.retained_structured,
    }


def _apply_tool_history_compression(
    host: Any, *, context: RunContext, request: Any
) -> None:
    """Epic 035 A pre-pass: shrink OLD tool-result bulk by tier, LLM-free.

    Mirrors E5's cheap in-place shrink but targets tool_result CONTENT (not call
    args): for stateless/no-cache providers, older tool results are truncated then
    stubbed by tier so the rewritten prefix stays bounded. Idempotent; records the
    savings under ``tool_history_compression``.
    """
    from agent_driver.context.compaction.tool_history import (  # pylint: disable=import-outside-toplevel
        compress_tool_history,
    )

    window = int(getattr(host._config, "context_window_estimate", 0) or 0)
    messages, audit = compress_tool_history(
        list(request.messages), effective_window=window
    )
    if not audit.get("activated"):
        return
    request.messages = messages
    context.metadata["tool_history_compression"] = {
        "chars_saved": audit.get("chars_saved", 0),
        "truncated": audit.get("truncated", 0),
        "stubbed": audit.get("stubbed", 0),
    }


def _apply_live_tool_result_prune(
    host: Any, *, context: RunContext, request: Any, token_pressure_state: str
) -> None:
    """EPIC-08: promote ``ToolResultPruner`` to a live, pressure-gated default tier.

    Clears the CONTENT of OLD tool results (keeping the newest ``keep_recent``) in the
    EPHEMERAL request only — the durable log is untouched, so nothing is permanently
    lost and a resume rebuilds the full history. Runs independently of
    ``enable_compaction`` (like the tool-arg/tool-history pre-passes above), but only
    under token pressure and only when it frees at least ``live_tool_prune_min_chars``
    (so it never churns the prompt-cache prefix for a negligible gain). Idempotent.
    """
    if token_pressure_state not in {"compact_recommended", "blocking"}:
        return
    from agent_driver.context.compaction.tool_clear import (  # pylint: disable=import-outside-toplevel
        clear_old_tool_results,
    )

    keep_recent = int(getattr(host._config, "live_tool_prune_keep_recent", 3) or 3)
    min_chars = int(getattr(host._config, "live_tool_prune_min_chars", 2000) or 0)
    result = clear_old_tool_results(list(request.messages), keep_recent=keep_recent)
    if result.cleared <= 0 or result.chars_saved < min_chars:
        # Nothing old to clear, or the gain is below the commit threshold — leave the
        # request (and its cache prefix) untouched.
        return
    request.messages = result.messages
    context.metadata["live_tool_prune"] = {
        "cleared": result.cleared,
        "chars_saved": result.chars_saved,
        "keep_recent": keep_recent,
        "token_pressure_state": token_pressure_state,
    }


class CompactionStageHost(Protocol):
    """Host surface required for compaction stage helpers."""

    _deps: RunnerDeps
    _config: RunnerConfig
    _compaction_orchestrator: CompactionOrchestrator | None

    def _get_compaction_orchestrator(self) -> CompactionOrchestrator: ...
    def _emit(self, event: EventSpec) -> None: ...


def _emit_compaction_started(
    host: CompactionStageHost,
    *,
    context: RunContext,
    decision: CompactionDecision,
    compaction_id: str,
    token_pressure_state: str,
    orchestrator: CompactionOrchestrator,
) -> None:
    """Emit the start of an eligible compaction attempt.

    `memory_compacted` remains the canonical terminal outcome event. This
    lifecycle event exists so host UIs can show a non-intrusive "context is
    being summarized" status while LLM/full compaction is still running.
    """
    host._emit(
        EventSpec(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            event_type=RuntimeEventType.MEMORY_COMPACTION_STARTED,
            payload={
                "compaction_id": compaction_id,
                "mode": decision.mode.value,
                "reason": "token_pressure",
                "token_pressure_state": token_pressure_state,
                "compaction_state": orchestrator.state_snapshot(),
            },
        )
    )


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


def _finalize_ineligible_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    decision: CompactionDecision,
    orchestrator: CompactionOrchestrator,
) -> None:
    """Record the no-op audit and emit a ``skipped`` outcome for a decision that
    was not eligible to compact."""
    context.metadata[COMPACTION_FAILURES_KEY] = []
    context.metadata[COMPACTION_AUDIT_KEY] = {
        "decision": context.metadata[COMPACTION_DECISION_KEY]
    }
    skip_payload: dict[str, Any] = {"mode": decision.mode.value}
    if decision.skip_reason is not None:
        skip_payload["skip_reason"] = decision.skip_reason.value
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="skipped",
        payload_extras=skip_payload,
        orchestrator=orchestrator,
    )


async def _run_compaction_mode_dispatch(
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
    """Run the compaction mode decision tree (session_memory -> llm_full -> partial,
    with the documented fallthroughs: a failed/absent session_memory attempt falls
    through to llm_full when enabled, and partial runs for an explicit partial mode
    or as the last resort when llm_full was not attempted). Return True when one mode
    fully handled the attempt; False means no path applied (the caller then records
    a ``path_not_implemented`` failure)."""
    # Option B1b (C2): route transcript compaction through the cost-ordered
    # CondenserPipeline when opted in. session_memory stays on the legacy plane
    # (its side-channel persistence + freshness are orthogonal to transcript trim).
    if (
        getattr(host._config, "use_condenser_pipeline", False)
        and decision.mode.value != "session_memory"
    ):
        return await _run_condenser_pipeline_dispatch(
            host,
            context=context,
            request=request,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
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
            return True
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
                return True
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
            return True
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
            return True
    return False


async def _run_condenser_pipeline_dispatch(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    """Option-B1b transcript compaction: run the model-free CondenserPipeline
    cheapest-first, and only when it does not reach the target delegate to the
    mature ``_apply_llm_full_compaction`` path. The novel win is skipping the LLM
    summary entirely whenever clearing/truncating old tool bulk already fits."""
    original_view = list(request.messages)
    before_chars = message_chars(original_view)
    ctx = CondenseContext(
        target_chars=_pipeline_target_chars(host, context=context),
        chars_per_token=_COMPACTION_CHARS_PER_TOKEN,
        extras={"effective_window_tokens": _effective_window_tokens(context)},
    )
    pipeline = CondenserPipeline(default_condenser_tiers())
    result = await pipeline.run(original_view, ctx=ctx)
    freed_chars = before_chars - message_chars(result.messages)

    if result.fit and freed_chars > 0:
        request.messages = result.messages
        return _finalize_pipeline_outcome(
            host,
            context=context,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            freed_chars=freed_chars,
            applied=result.applied,
            fit=True,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
    # Model-free tiers did not fit. The request is still the original here, so the
    # mature llm_full path can re-summarize from scratch when the LLM tier is on.
    if host._config.enable_llm_compaction:
        return await _apply_llm_full_compaction(
            host,
            context=context,
            request=request,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
    # No LLM tier: keep whatever real progress the model-free tiers made (honest
    # ``fit=False``), else record a neutral skip that leaves the View untouched.
    if freed_chars > 0:
        request.messages = result.messages
        return _finalize_pipeline_outcome(
            host,
            context=context,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            freed_chars=freed_chars,
            applied=result.applied,
            fit=False,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
    return _finalize_pipeline_no_progress(
        host,
        context=context,
        orchestrator=orchestrator,
        decision=decision,
        compaction_id=compaction_id,
        circuit_breaker_open_before=circuit_breaker_open_before,
    )


def _finalize_pipeline_outcome(
    host: CompactionStageHost,
    *,
    context: RunContext,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    freed_chars: int,
    applied: list[dict[str, Any]],
    fit: bool,
    circuit_breaker_open_before: bool,
) -> bool:
    """Record a successful condenser-pipeline compaction (model-free tiers only)."""
    tiers = _applied_tier_names(applied)
    result_payload = {
        "compaction_id": compaction_id,
        "mode": "partial",
        "success": True,
        "metadata": {
            "strategy": "condenser_pipeline",
            "chars_freed": freed_chars,
            "fit": fit,
            "tiers": tiers,
        },
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
        decision=decision, result=_result_from_payload(result_payload)
    )
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="successful",
        payload_extras={
            "mode": "condenser_pipeline",
            "compaction_id": compaction_id,
            "chars_freed": freed_chars,
            "fit": fit,
            "tiers": tiers,
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


def _finalize_pipeline_no_progress(
    host: CompactionStageHost,
    *,
    context: RunContext,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    """Neutral ``skipped`` when the model-free tiers freed nothing and no LLM tier is
    available — like the partial no-progress path, it neither resets the breaker nor
    counts as a failure, and leaves the request View untouched."""
    context.metadata[COMPACTION_RESULT_KEY] = {
        "compaction_id": compaction_id,
        "mode": "partial",
        "success": False,
        "metadata": {"strategy": "condenser_pipeline", "chars_freed": 0},
    }
    context.metadata[COMPACTION_FAILURES_KEY] = []
    audit = orchestrator.complete_attempt(decision=decision, result=None)
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="skipped",
        payload_extras={
            "mode": "condenser_pipeline",
            "compaction_id": compaction_id,
            "skip_reason": "insufficient_progress",
            "chars_freed": 0,
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


def _finalize_unimplemented_compaction_path(
    host: CompactionStageHost,
    *,
    context: RunContext,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> None:
    """No compaction mode applied (a disabled/misrouted path): record + emit a
    ``path_not_implemented`` failed attempt so the circuit breaker still sees it."""
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


def _reset_rolling_summary_state(context: RunContext) -> None:
    """Clear the B2 rolling-summary cursor + cadence counter — used when another
    compaction plane (session_memory) supersedes the transcript and the rolling
    offsets no longer line up."""
    for key in (
        "rolling_summary",
        "rolling_summary_covers_upto",
        "rolling_skip_count",
    ):
        context.metadata.pop(key, None)


def _rolling_cadence_should_defer(
    host: CompactionStageHost,
    *,
    context: RunContext,
    decision: CompactionDecision,
    token_pressure_state: str,
) -> bool:
    """Option B2 cadence: with ``rolling_summary_every_n_turns > 1``, defer the fold on
    non-cadence firings so the prompt-cache prefix is rewritten every N eligible firings
    instead of every one (fewer cache breaks, higher interim occupancy — the documented
    trade). Never defers the first fold (no prior summary to reuse) and never under
    blocking pressure (correctness over cache). Returns True to skip this firing."""
    if not (
        host._config.enable_compaction
        and host._config.enable_llm_compaction
        and host._config.enable_rolling_summary
        and decision.eligible
        and decision.mode.value == "llm_full"
    ):
        return False
    every_n = int(host._config.rolling_summary_every_n_turns or 1)
    if every_n <= 1 or not context.metadata.get("rolling_summary"):
        return False
    if token_pressure_state == "blocking":
        context.metadata["rolling_skip_count"] = 0
        return False
    skip_count = int(context.metadata.get("rolling_skip_count", 0) or 0)
    if skip_count + 1 < every_n:
        context.metadata["rolling_skip_count"] = skip_count + 1
        return True
    context.metadata["rolling_skip_count"] = 0
    return False


async def apply_compaction_if_eligible(
    host: CompactionStageHost,
    *,
    context: RunContext,
    request: Any,
    token_pressure_state: str,
) -> None:
    """Run compaction orchestration before final provider completion."""
    if host._config.enable_tool_arg_truncation:
        _apply_tool_arg_truncation(host, context=context, request=request)
    if getattr(host._config, "enable_tool_history_compression", False):
        _apply_tool_history_compression(host, context=context, request=request)
    # EPIC-08: live tool-result prune — independent of enable_compaction, pressure-gated.
    if getattr(host._config, "live_tool_prune_enabled", False):
        _apply_live_tool_result_prune(
            host,
            context=context,
            request=request,
            token_pressure_state=token_pressure_state,
        )
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
        _finalize_ineligible_compaction(
            host, context=context, decision=decision, orchestrator=orchestrator
        )
        return
    if _rolling_cadence_should_defer(
        host,
        context=context,
        decision=decision,
        token_pressure_state=token_pressure_state,
    ):
        # Cadence defer: leave the prompt uncompacted this firing (no aux call, no
        # prefix rewrite); the accumulated slice folds in on the next cadence firing.
        context.metadata["rolling_cadence_deferred"] = True
        return
    context.metadata["rolling_cadence_deferred"] = False
    circuit_breaker_open_before = bool(
        orchestrator.state_snapshot().get("circuit_breaker_open")
    )
    compaction_id = orchestrator.start_attempt()
    context.metadata["active_compaction_id"] = compaction_id
    _emit_compaction_started(
        host,
        context=context,
        decision=decision,
        compaction_id=compaction_id,
        token_pressure_state=token_pressure_state,
        orchestrator=orchestrator,
    )
    handled = await _run_compaction_mode_dispatch(
        host,
        context=context,
        request=request,
        session_memory=session_memory,
        orchestrator=orchestrator,
        decision=decision,
        compaction_id=compaction_id,
        circuit_breaker_open_before=circuit_breaker_open_before,
    )
    if handled:
        return
    _finalize_unimplemented_compaction_path(
        host,
        context=context,
        orchestrator=orchestrator,
        decision=decision,
        compaction_id=compaction_id,
        circuit_breaker_open_before=circuit_breaker_open_before,
    )


def _finalize_empty_session_memory_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    """Empty-result guard (epic 017): session-memory compaction can produce an
    empty/system-only message set on large corpus-bound contexts; applying it gets
    the provider's «Input required» rejection — hosts responded by disabling
    compaction wholesale (MeetScript chat_v2). Keep the original prompt, count the
    attempt as failed (feeds the circuit breaker), emit a distinct signal."""
    failure = {
        "kind": "empty_compaction_result",
        "mode": "session_memory",
        "message": "compaction produced no sendable messages; original prompt kept",
    }
    audit = orchestrator.complete_attempt(decision=decision, failures=[failure])
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    context.metadata[COMPACTION_FAILURES_KEY] = [failure]
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="failed",
        payload_extras={
            "mode": "session_memory",
            "compaction_id": compaction_id,
            "failure_kind": failure["kind"],
            "failure_message": failure["message"],
            "signal_id": "compaction_empty_result_skipped",
        },
        orchestrator=orchestrator,
    )
    _maybe_emit_circuit_breaker_warning(
        host,
        context=context,
        before_open=circuit_breaker_open_before,
        orchestrator=orchestrator,
    )
    return True  # attempt fully handled (as a failure); prompt left intact


def _finalize_successful_session_memory_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    compacted: Any,
    freshness: Any,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    """Record + emit a successful session-memory compaction: result/audit metadata,
    retained digest/artifact ids, post-compact cleanup, and the successful outcome."""
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
    # B2 marker coexistence: session_memory rewrote the message set, so any rolling
    # summary + cursor from a prior llm_full firing is now stale (its group offsets no
    # longer line up). Reset the rolling state so the next llm_full fold starts fresh
    # instead of folding onto a summary that misses this session-memory compaction.
    _reset_rolling_summary_state(context)
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
    compacted_messages = [
        ChatMessage.model_validate(item) for item in compacted.prompt_messages
    ]
    if not _has_sendable_content(compacted_messages):
        return _finalize_empty_session_memory_compaction(
            host,
            context=context,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
    request.messages = compacted_messages
    return _finalize_successful_session_memory_compaction(
        host,
        context=context,
        compacted=compacted,
        freshness=freshness,
        orchestrator=orchestrator,
        decision=decision,
        compaction_id=compaction_id,
        circuit_breaker_open_before=circuit_breaker_open_before,
    )


def _finalize_failed_llm_full_compaction(
    host: CompactionStageHost,
    *,
    context: RunContext,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_result: Any,
    compaction_id: str,
    circuit_breaker_open_before: bool,
) -> bool:
    """Record + emit an unsuccessful llm_full compaction attempt (including the
    honest aux idle-timeout failure kind) and signal the caller to stop."""
    # Epic 041 C: a liveness idle-timeout is a distinct, honest failure kind so
    # hosts can tell "summary provider stalled" from a content/parse failure.
    result_meta = (
        compaction_result.metadata if compaction_result is not None else {}
    ) or {}
    idle_timed_out = result_meta.get("failure_kind") == "aux_idle_timeout"
    failure = {
        "kind": (
            "llm_compaction_aux_idle_timeout"
            if idle_timed_out
            else "llm_compaction_failed"
        ),
        "mode": "llm_full",
        "message": (
            "compaction summary provider stalled (idle timeout)"
            if idle_timed_out
            else "provider compaction returned unsuccessful result"
        ),
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


def _record_llm_full_result_metadata(
    host: CompactionStageHost,
    *,
    context: RunContext,
    compaction_result: Any,
    excerpt: "_FullCompactionExcerpt",
    unit_receipt: dict[str, Any],
) -> None:
    """Write the successful-compaction result + audit metadata, folding in the
    ptl-retry group-drop receipt (kept/dropped/protected group hashes + budget)."""
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
                "dropped_groups": len(excerpt.dropped_groups),
                "dropped_group_sha256": [
                    hashlib.sha256(item.encode("utf-8")).hexdigest()
                    for item in excerpt.dropped_groups
                ],
                "kept_groups": len(excerpt.kept_groups),
                "protected_groups": len(excerpt.protected_indexes),
                "max_chars": excerpt.max_chars,
                "budget_source": excerpt.budget_source,
                "budget_overrun_chars": max(
                    0,
                    sum(len(item) for item in excerpt.kept_groups) - excerpt.max_chars,
                ),
                **unit_receipt,
            },
        }


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
    original_messages = list(request.messages)
    # Option B2: in rolling mode, fold the persisted prior summary + only the groups
    # past the cursor instead of re-summarising the full history each firing. The
    # first firing (cursor 0, no prior summary) degrades to a normal full compaction.
    rolling = bool(host._config.enable_rolling_summary)
    prior_summary: str | None = None
    covers_upto = 0
    if rolling:
        prior_raw = context.metadata.get("rolling_summary")
        prior_summary = prior_raw if isinstance(prior_raw, str) and prior_raw else None
        covers_upto = int(context.metadata.get("rolling_summary_covers_upto", 0) or 0)
    excerpt = _build_full_compaction_excerpt(
        host,
        context=context,
        request=request,
        skip_leading_groups=covers_upto if rolling else 0,
    )
    compaction_provider, compaction_model = _resolve_compaction_backend(
        host, request=request
    )
    compaction_result, summary = await run_full_llm_compaction(
        provider=compaction_provider,
        model=compaction_model,
        history_excerpt=excerpt.sanitized_excerpt,
        user_request=context.run_input.input or "",
        idle_timeout_seconds=host._config.aux_idle_timeout_seconds,
        max_history_chars=excerpt.max_chars,
        history_is_bounded=True,
        prior_summary=prior_summary,
    )
    _account_compaction_cost(context, compaction_result, provider=compaction_provider)
    if compaction_result is None or not compaction_result.success:
        return _finalize_failed_llm_full_compaction(
            host,
            context=context,
            orchestrator=orchestrator,
            decision=decision,
            compaction_result=compaction_result,
            compaction_id=compaction_id,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
    compaction_result = compaction_result.model_copy(
        update={"compaction_id": compaction_id}
    )
    if rolling:
        # Persist the updated rolling summary + advance the cursor to the full source
        # group count; the next firing folds only the groups that arrive after this.
        context.metadata["rolling_summary"] = json.dumps(
            summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        context.metadata["rolling_summary_covers_upto"] = excerpt.total_source_groups
    retained_messages = _retained_messages_after_full_compaction(original_messages)
    request.messages = _splice_summary_message(retained_messages, summary)
    unit_receipt = _material_unit_receipt(
        original_messages=original_messages,
        retained_messages=retained_messages,
        pre_summary_groups_dropped=bool(excerpt.dropped_groups),
    )
    _record_llm_full_result_metadata(
        host,
        context=context,
        compaction_result=compaction_result,
        excerpt=excerpt,
        unit_receipt=unit_receipt,
    )
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
    original_view = list(request.messages)
    before_chars = message_chars(original_view)
    compacted = build_partial_compaction(
        messages=[msg.model_dump(mode="json") for msg in original_view],
        retain_recent_messages=6,
        prefix_mode=True,
    )
    compacted_view = [
        ChatMessage.model_validate(item) for item in compacted.prompt_messages
    ]
    # BUG-7 (compaction hardening C1): partial used to report ``success=True``
    # unconditionally, resetting the circuit breaker even for an explicit no-op or
    # a rewrite that freed no space. Report success only on real token progress;
    # otherwise leave the view untouched and record an honest ``skipped`` outcome
    # that neither resets the breaker (false clear) nor increments it (unfair
    # failure) — a no-op means nothing was eligible, not that compaction failed.
    strategy = str(compacted.metadata.get("strategy", ""))
    freed_chars = before_chars - message_chars(compacted_view)
    made_progress = strategy != "no_op" and freed_chars > 0
    if not made_progress:
        return _finalize_partial_no_progress(
            host,
            context=context,
            orchestrator=orchestrator,
            decision=decision,
            compaction_id=compaction_id,
            strategy=strategy,
            freed_chars=freed_chars,
            circuit_breaker_open_before=circuit_breaker_open_before,
        )
    request.messages = compacted_view
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
            "chars_freed": freed_chars,
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


def _finalize_partial_no_progress(
    host: CompactionStageHost,
    *,
    context: RunContext,
    orchestrator: CompactionOrchestrator,
    decision: CompactionDecision,
    compaction_id: str,
    strategy: str,
    freed_chars: int,
    circuit_breaker_open_before: bool,
) -> bool:
    """Record a partial attempt that freed no space as an honest ``skipped``.

    The request View is left untouched. ``complete_attempt`` is called with
    ``result=None`` so the outcome is neutral: it does not reset the circuit
    breaker (the old ``success=True`` bug) and does not count as a failure (a
    no-op means nothing was eligible to compact, not that compaction broke).
    """
    reason = "no_op" if strategy == "no_op" else "insufficient_progress"
    result_payload = {
        "compaction_id": compaction_id,
        "mode": "partial",
        "success": False,
        "metadata": {"strategy": strategy, "chars_freed": freed_chars},
    }
    context.metadata[COMPACTION_RESULT_KEY] = result_payload
    context.metadata[COMPACTION_FAILURES_KEY] = []
    audit = orchestrator.complete_attempt(decision=decision, result=None)
    context.metadata[COMPACTION_AUDIT_KEY] = audit.model_dump(mode="json")
    _emit_compaction_outcome(
        host,
        context=context,
        outcome="skipped",
        payload_extras={
            "mode": "partial",
            "compaction_id": compaction_id,
            "skip_reason": reason,
            "chars_freed": freed_chars,
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


__all__ = [
    "CompactionStageHost",
    "_emit_compaction_started",
    "apply_compaction_if_eligible",
]
