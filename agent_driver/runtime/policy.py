"""Pure observe-mode harness policy evaluator and signal mappers."""

from __future__ import annotations

from typing import Any, Iterable

from agent_driver.contracts.policy import (
    HarnessPolicyProfile,
    PolicyEvaluation,
    PolicySignal,
)
from agent_driver.observability.run_trace.provider import event_data
from agent_driver.observability.run_trace.tool_guardrails import (
    diagnostic_tool_guardrail_decisions,
)
from agent_driver.observability.provenance import build_provenance_summary

_DEFAULT_POLICY_IDS = (
    "provider_request_shape_preflight",
    "tool_loop_no_progress",
    "required_source_evidence",
    "workbook_context_required",
    "artifact_provenance_required",
    "side_effect_transaction_required",
    "budget_threshold",
    "user_steering_interrupt",
)


def default_observe_policy_profile(
    *,
    profile_id: str = "default-observe",
    enabled_policy_ids: Iterable[str] | None = None,
    required_evidence: Iterable[str] | None = None,
    rollout_tags: Iterable[str] | None = None,
) -> HarnessPolicyProfile:
    """Return the deterministic default observe-mode policy profile."""

    return HarnessPolicyProfile(
        profile_id=profile_id,
        mode="observe",
        enabled_policy_ids=list(enabled_policy_ids or _DEFAULT_POLICY_IDS),
        required_evidence=list(required_evidence or []),
        rollout_tags=list(rollout_tags or []),
    )


def policy_profile_from_metadata(metadata: dict[str, Any]) -> HarnessPolicyProfile | None:
    """Return opt-in policy profile from host/run metadata, if present."""

    raw = metadata.get("harness_policy_profile") or metadata.get("policy_profile")
    if not isinstance(raw, dict):
        return None
    return HarnessPolicyProfile.model_validate(raw)


def evaluate_policy_signals(
    signals: Iterable[PolicySignal | dict[str, Any]],
    *,
    profile: HarnessPolicyProfile | dict[str, Any] | None = None,
) -> list[PolicyEvaluation]:
    """Evaluate normalized signals without changing runtime behavior."""

    policy_profile = _profile(profile)
    normalized = [
        signal if isinstance(signal, PolicySignal) else PolicySignal.model_validate(signal)
        for signal in signals
    ]
    enabled = set(policy_profile.enabled_policy_ids or _DEFAULT_POLICY_IDS)
    evaluations: list[PolicyEvaluation] = []
    for policy_id, selected in _policy_matches(normalized).items():
        if policy_id not in enabled:
            continue
        action, reason = _policy_action(policy_id, selected)
        matched_signal_ids = [signal.signal_id for signal in selected]
        evaluations.append(
            PolicyEvaluation(
                evaluation_id=f"eval_{policy_id}_{len(evaluations) + 1}",
                policy_id=policy_id,
                profile_id=policy_profile.profile_id,
                mode=policy_profile.mode,
                status="matched",
                matched_signal_ids=matched_signal_ids,
                selected_action=action,
                confidence=_confidence(policy_id, selected),
                reason=reason,
                affected_tools=_dedupe(
                    tool for signal in selected for tool in signal.affected_tools
                ),
                affected_artifacts=_dedupe(
                    artifact
                    for signal in selected
                    for artifact in signal.affected_artifacts
                ),
                required_evidence=_dedupe(
                    evidence
                    for signal in selected
                    for evidence in signal.required_evidence
                ),
                observed_evidence=_dedupe(
                    evidence
                    for signal in selected
                    for evidence in signal.observed_evidence
                ),
                budget=_budget(policy_profile, policy_id),
                enforcement_skipped_reason=(
                    "observe_mode_no_runtime_effect"
                    if policy_profile.mode == "observe"
                    else None
                ),
                redacted_metadata={
                    "signal_reasons": _dedupe(signal.reason for signal in selected),
                    "rollout_tags": list(policy_profile.rollout_tags),
                },
            )
        )
    if evaluations:
        return evaluations
    return [
        PolicyEvaluation(
            evaluation_id="eval_no_policy_match_1",
            policy_id="no_policy_match",
            profile_id=policy_profile.profile_id,
            mode=policy_profile.mode,
            status="not_matched",
            selected_action="continue",
            reason="no_policy_ready_signals",
            enforcement_skipped_reason=(
                "observe_mode_no_runtime_effect"
                if policy_profile.mode == "observe"
                else None
            ),
        )
    ]


def map_trace_policy_signals(
    events: list[dict[str, object]],
    *,
    run_id: str,
    task_contract: dict[str, Any] | None = None,
    required_evidence: Iterable[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[PolicySignal]:
    """Map existing trace diagnostics into normalized policy signals."""

    signals: list[PolicySignal] = []
    signals.extend(_provider_signals(events, run_id=run_id))
    signals.extend(_runtime_decision_signals(events, run_id=run_id))
    signals.extend(_tool_guardrail_signals(events, run_id=run_id, task_contract=task_contract))
    provenance = build_provenance_summary(
        events=events,
        metadata=metadata or _metadata_from_events(events),
        required_evidence=list(required_evidence or _required_evidence(task_contract)),
    )
    signals.extend(_provenance_signals(provenance, run_id=run_id))
    signals.extend(_budget_signals(events, run_id=run_id))
    signals.extend(_control_signals(events, run_id=run_id))
    return _dedupe_signals(signals)


def build_observe_policy_summary(
    *,
    events: list[dict[str, object]],
    run_id: str,
    profile: HarnessPolicyProfile | dict[str, Any] | None = None,
    task_contract: dict[str, Any] | None = None,
    required_evidence: Iterable[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redaction-safe observe-mode policy block for traces/bundles."""

    policy_profile = _profile(profile)
    selected_required_evidence = (
        list(required_evidence)
        if required_evidence is not None
        else (policy_profile.required_evidence or None)
    )
    signals = map_trace_policy_signals(
        events,
        run_id=run_id,
        task_contract=task_contract,
        required_evidence=selected_required_evidence,
        metadata=metadata,
    )
    evaluations = evaluate_policy_signals(signals, profile=policy_profile)
    matched = [item for item in evaluations if item.status == "matched"]
    return {
        "profile": policy_profile.model_dump(mode="json"),
        "signals": [item.model_dump(mode="json") for item in signals],
        "evaluations": [item.model_dump(mode="json") for item in evaluations],
        "count": len(evaluations),
        "matched_count": len(matched),
        "would_fire_policy_ids": [item.policy_id for item in matched],
        "selected_actions": [item.selected_action for item in matched],
        "observe_mode": policy_profile.mode == "observe",
    }


def _provider_signals(events: list[dict[str, object]], *, run_id: str) -> list[PolicySignal]:
    signals: list[PolicySignal] = []
    for index, event in enumerate(events, start=1):
        name = event.get("event")
        data = event_data(event)
        seq = _seq(event, index)
        if name == "llm_request_rejected":
            signals.append(
                PolicySignal(
                    signal_id=f"sig_provider_rejected_{seq}",
                    signal_class="provider_error",
                    reason=str(data.get("reason") or "provider_request_rejected"),
                    severity="error",
                    source="llm",
                    run_id=run_id,
                    seq=seq,
                    redacted_metadata=_metadata_subset(data, ("error_code", "status")),
                )
            )
        if name != "llm_call_completed":
            continue
        preflight = data.get("provider_preflight")
        if not isinstance(preflight, dict):
            continue
        preflight_block = preflight.get("preflight")
        request_shape = preflight.get("request_shape")
        status = (
            preflight_block.get("status")
            if isinstance(preflight_block, dict)
            else preflight.get("status")
        )
        downgrades = (
            preflight_block.get("downgrades")
            if isinstance(preflight_block, dict)
            else preflight.get("downgrades")
        )
        request_shape_degraded = (
            isinstance(request_shape, dict)
            and any("downgraded" in str(value) for value in request_shape.values())
        )
        if (
            status in {"degraded", "failed"}
            or (isinstance(downgrades, list) and downgrades)
            or request_shape_degraded
        ):
            signals.append(
                PolicySignal(
                    signal_id=f"sig_provider_preflight_{seq}",
                    signal_class="provider_preflight",
                    reason="provider_request_shape_degraded",
                    severity="warning" if status == "degraded" else "error",
                    source="llm",
                    run_id=run_id,
                    seq=seq,
                    redacted_metadata={
                        "status": status,
                        "downgrades": downgrades if isinstance(downgrades, list) else [],
                        "request_shape": request_shape if isinstance(request_shape, dict) else {},
                    },
                )
            )
    return signals


def _runtime_decision_signals(
    events: list[dict[str, object]], *, run_id: str
) -> list[PolicySignal]:
    signals: list[PolicySignal] = []
    for index, event in enumerate(events, start=1):
        if event.get("event") != "runtime_decision":
            continue
        data = event_data(event)
        metadata = data.get("redacted_metadata")
        if isinstance(metadata, dict) and metadata.get("policy_observe_projection"):
            continue
        kind = data.get("kind")
        action = data.get("action")
        reason = data.get("reason")
        status = data.get("status")
        if kind not in {"tool_guardrail", "evidence", "budget", "approval", "steering"}:
            continue
        signal_class = "runtime_decision"
        if kind == "tool_guardrail":
            signal_class = "tool_guardrail"
        elif kind == "evidence":
            signal_class = "provenance_contract"
        elif kind == "budget":
            signal_class = "usage_budget"
        elif kind == "steering":
            signal_class = "user_control"
        signals.append(
            PolicySignal(
                signal_id=f"sig_runtime_decision_{data.get('decision_id') or _seq(event, index)}",
                signal_class=signal_class,
                reason=str(reason or f"{kind}_{action}_{status}"),
                severity="warning" if status != "failed" else "error",
                source="runtime_decision",
                run_id=run_id,
                attempt_id=data.get("attempt_id") if isinstance(data.get("attempt_id"), str) else None,
                seq=_seq(event, index),
                affected_tools=[
                    item for item in data.get("affected_tools", []) if isinstance(item, str)
                ],
                required_evidence=[
                    item for item in data.get("required_evidence", []) if isinstance(item, str)
                ],
                observed_evidence=[
                    item for item in data.get("observed_evidence", []) if isinstance(item, str)
                ],
                redacted_metadata=_metadata_subset(data, ("kind", "action", "status", "policy_id")),
            )
        )
    return signals


def _tool_guardrail_signals(
    events: list[dict[str, object]],
    *,
    run_id: str,
    task_contract: dict[str, Any] | None,
) -> list[PolicySignal]:
    signals: list[PolicySignal] = []
    for decision in diagnostic_tool_guardrail_decisions(
        events,
        run_id=run_id,
        task_contract=task_contract,
    ):
        reason = str(decision.get("reason") or "tool_guardrail")
        signals.append(
            PolicySignal(
                signal_id=f"sig_tool_guardrail_{reason}_{len(signals) + 1}",
                signal_class="tool_guardrail",
                reason=reason,
                severity="warning",
                source="diagnostic_tool_guardrail",
                run_id=run_id,
                affected_tools=[
                    item
                    for item in decision.get("affected_tools", [])
                    if isinstance(item, str)
                ],
                required_evidence=[
                    item
                    for item in decision.get("required_evidence", [])
                    if isinstance(item, str)
                ],
                observed_evidence=[
                    item
                    for item in decision.get("observed_evidence", [])
                    if isinstance(item, str)
                ],
                redacted_metadata=decision.get("redacted_metadata")
                if isinstance(decision.get("redacted_metadata"), dict)
                else {},
            )
        )
    return signals


def _provenance_signals(provenance: dict[str, Any], *, run_id: str) -> list[PolicySignal]:
    verdicts = provenance.get("contract_verdicts")
    if not isinstance(verdicts, dict):
        return []
    violations = verdicts.get("violations")
    if not isinstance(violations, dict):
        return []
    required = verdicts.get("required_evidence")
    required_evidence = [item for item in required if isinstance(item, str)] if isinstance(required, list) else []
    signals: list[PolicySignal] = []
    for key, value in sorted(violations.items()):
        if value is not True:
            continue
        signals.append(
            PolicySignal(
                signal_id=f"sig_provenance_{key}",
                signal_class="provenance_contract",
                reason=key,
                severity="error",
                source="provenance_contract",
                run_id=run_id,
                required_evidence=required_evidence,
                redacted_metadata={"contract_status": verdicts.get("status")},
            )
        )
    return signals


def _budget_signals(events: list[dict[str, object]], *, run_id: str) -> list[PolicySignal]:
    signals: list[PolicySignal] = []
    for index, event in enumerate(events, start=1):
        if event.get("event") not in {"warning", "runtime_decision"}:
            continue
        data = event_data(event)
        reason = str(data.get("reason") or data.get("code") or "")
        if "budget" not in reason and "cost" not in reason:
            continue
        signals.append(
            PolicySignal(
                signal_id=f"sig_budget_{_seq(event, index)}",
                signal_class="usage_budget",
                reason=reason or "budget_threshold",
                severity="warning",
                source="runtime",
                run_id=run_id,
                seq=_seq(event, index),
                redacted_metadata=_metadata_subset(data, ("cost_usd_estimate", "limit")),
            )
        )
    return signals


def _control_signals(events: list[dict[str, object]], *, run_id: str) -> list[PolicySignal]:
    signals: list[PolicySignal] = []
    for index, event in enumerate(events, start=1):
        if event.get("event") not in {"control_requested", "control_applied"}:
            continue
        data = event_data(event)
        kind = data.get("kind")
        if kind not in {"interrupt", "enqueue_user_message", "set_goal", "patch_planning_state"}:
            continue
        signals.append(
            PolicySignal(
                signal_id=f"sig_user_control_{_seq(event, index)}",
                signal_class="user_control",
                reason=f"user_control_{kind}",
                severity="info",
                source="control_queue",
                run_id=run_id,
                seq=_seq(event, index),
                redacted_metadata=_metadata_subset(data, ("kind", "priority")),
            )
        )
    return signals


def _policy_matches(signals: list[PolicySignal]) -> dict[str, list[PolicySignal]]:
    matches: dict[str, list[PolicySignal]] = {}
    for signal in signals:
        policy_id = _policy_for_signal(signal)
        if policy_id is None:
            continue
        matches.setdefault(policy_id, []).append(signal)
    return matches


def _policy_for_signal(signal: PolicySignal) -> str | None:
    if signal.signal_class in {"provider_preflight", "provider_error"}:
        return "provider_request_shape_preflight"
    if signal.reason in {
        "idempotent_read_no_progress",
        "repeated_identical_tool_args",
        "repeated_failed_tool_call",
    }:
        return "tool_loop_no_progress"
    if signal.reason in {"missing_source_evidence", "missing_required_tool_evidence"}:
        return "required_source_evidence"
    if signal.reason == "missing_context_provenance":
        return "workbook_context_required"
    if signal.reason == "missing_artifact_provenance":
        return "artifact_provenance_required"
    if signal.reason == "unsafe_side_effect_without_transaction_projection":
        return "side_effect_transaction_required"
    if signal.signal_class == "usage_budget":
        return "budget_threshold"
    if signal.signal_class == "user_control":
        return "user_steering_interrupt"
    return None


def _policy_action(policy_id: str, signals: list[PolicySignal]) -> tuple[str, str]:
    if policy_id == "provider_request_shape_preflight":
        if any(signal.signal_class == "provider_error" for signal in signals):
            return "switch_provider_route", "provider_rejected_or_route_degraded"
        return "reshape_request", "provider_preflight_would_reshape_request"
    if policy_id == "tool_loop_no_progress":
        return "ask_user", "tool_loop_no_progress_would_ask_or_stop"
    if policy_id == "required_source_evidence":
        return "mark_blocked", "required_source_evidence_missing"
    if policy_id == "workbook_context_required":
        return "mark_blocked", "required_workbook_context_missing"
    if policy_id == "artifact_provenance_required":
        return "mark_blocked", "required_artifact_provenance_missing"
    if policy_id == "side_effect_transaction_required":
        return "rollback", "side_effect_transaction_missing"
    if policy_id == "budget_threshold":
        return "force_final", "budget_threshold_would_force_final"
    if policy_id == "user_steering_interrupt":
        return "interrupt_for_approval", "user_control_would_interrupt_or_steer"
    return "warn", "policy_would_warn"


def _confidence(policy_id: str, signals: list[PolicySignal]) -> float:
    if policy_id in {"provider_request_shape_preflight", "side_effect_transaction_required"}:
        return 0.9
    if policy_id in {
        "required_source_evidence",
        "workbook_context_required",
        "artifact_provenance_required",
    }:
        return 0.85
    if policy_id == "tool_loop_no_progress" and len(signals) > 1:
        return 0.8
    return 0.7


def _profile(profile: HarnessPolicyProfile | dict[str, Any] | None) -> HarnessPolicyProfile:
    if profile is None:
        return default_observe_policy_profile()
    if isinstance(profile, HarnessPolicyProfile):
        return profile
    return HarnessPolicyProfile.model_validate(profile)


def _budget(profile: HarnessPolicyProfile, policy_id: str) -> dict[str, Any]:
    value = profile.budgets.get(policy_id)
    return value if isinstance(value, dict) else {}


def _metadata_from_events(events: list[dict[str, object]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for event in events:
        data = event_data(event)
        if event.get("event") == "run_started":
            for key in (
                "context_provenance",
                "skill_attachments",
                "artifact_provenance",
                "source_evidence",
                "side_effect_transactions",
                "required_evidence",
            ):
                if key in data:
                    metadata[key] = data[key]
        event_metadata = data.get("metadata")
        if isinstance(event_metadata, dict):
            metadata.update(event_metadata)
    return metadata


def _required_evidence(task_contract: dict[str, Any] | None) -> list[str]:
    if not isinstance(task_contract, dict):
        return []
    value = task_contract.get("required_evidence")
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _dedupe_signals(signals: list[PolicySignal]) -> list[PolicySignal]:
    seen: set[tuple[str, str, str]] = set()
    result: list[PolicySignal] = []
    for signal in signals:
        key = (signal.signal_class, signal.reason, ",".join(signal.affected_tools))
        if key in seen:
            continue
        seen.add(key)
        result.append(signal)
    return result


def _seq(event: dict[str, object], fallback: int) -> int:
    value = event.get("seq")
    return value if isinstance(value, int) and value > 0 else fallback


def _metadata_subset(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in keys if key in data}


__all__ = [
    "build_observe_policy_summary",
    "default_observe_policy_profile",
    "evaluate_policy_signals",
    "map_trace_policy_signals",
    "policy_profile_from_metadata",
]
