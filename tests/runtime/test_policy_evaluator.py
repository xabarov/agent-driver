"""Deterministic observe-mode policy evaluator scenarios."""

from __future__ import annotations

import pytest

from agent_driver.contracts.policy import HarnessPolicyProfile, PolicyAction, PolicySignal
from agent_driver.runtime.policy import (
    build_observe_policy_summary,
    evaluate_policy_signals,
    map_trace_policy_signals,
)


def test_provider_preflight_downgrade_would_reshape_request() -> None:
    summary = build_observe_policy_summary(
        run_id="run_policy_provider",
        events=[
            {
                "event": "llm_call_completed",
                "seq": 1,
                "data": {
                    "provider_preflight": {
                        "preflight": {
                            "status": "degraded",
                            "downgrades": ["forced_tool_choice"],
                        },
                        "request_shape": {
                            "tool_choice_policy": (
                                "forced_tool_choice_downgraded_to_auto"
                            )
                        },
                    }
                },
            }
        ],
    )

    assert summary["would_fire_policy_ids"] == ["provider_request_shape_preflight"]
    assert summary["selected_actions"] == ["reshape_request"]
    evaluation = summary["evaluations"][0]
    assert evaluation["mode"] == "observe"
    assert evaluation["enforcement_skipped_reason"] == "observe_mode_no_runtime_effect"


def test_repeated_read_no_progress_would_ask_user() -> None:
    events = [
        {
            "event": "tool_call_completed",
            "data": {
                "tools": [
                    {
                        "tool_name": "web_fetch",
                        "status": "completed",
                        "args": {"url": "https://example.com/a"},
                        "result_summary": "same page",
                    }
                ]
            },
        },
        {
            "event": "tool_call_completed",
            "data": {
                "tools": [
                    {
                        "tool_name": "web_fetch",
                        "status": "completed",
                        "args": {"url": "https://example.com/a"},
                        "result_summary": "same page",
                    }
                ]
            },
        },
    ]

    summary = build_observe_policy_summary(run_id="run_policy_loop", events=events)

    assert "tool_loop_no_progress" in summary["would_fire_policy_ids"]
    assert "ask_user" in summary["selected_actions"]


def test_missing_source_evidence_would_mark_blocked() -> None:
    summary = build_observe_policy_summary(
        run_id="run_policy_source",
        events=[{"event": "run_completed", "data": {}}],
        task_contract={"required_evidence": ["source_evidence"]},
    )

    assert summary["would_fire_policy_ids"] == ["required_source_evidence"]
    assert summary["selected_actions"] == ["mark_blocked"]
    assert {signal["reason"] for signal in summary["signals"]} == {
        "missing_required_tool_evidence",
        "missing_source_evidence",
    }


def test_missing_workbook_context_would_mark_blocked() -> None:
    signals = map_trace_policy_signals(
        events=[{"event": "run_completed", "data": {}}],
        run_id="run_policy_workbook",
        required_evidence=["context_provenance"],
    )

    evaluations = evaluate_policy_signals(signals)

    assert evaluations[0].policy_id == "workbook_context_required"
    assert evaluations[0].selected_action == "mark_blocked"


def test_missing_artifact_provenance_would_mark_blocked() -> None:
    signals = map_trace_policy_signals(
        events=[{"event": "run_completed", "data": {}}],
        run_id="run_policy_artifact",
        required_evidence=["artifact_provenance"],
    )

    evaluations = evaluate_policy_signals(signals)

    assert evaluations[0].policy_id == "artifact_provenance_required"
    assert evaluations[0].selected_action == "mark_blocked"


def test_missing_side_effect_transaction_would_rollback() -> None:
    signals = map_trace_policy_signals(
        events=[
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "excel_apply_edit",
                            "status": "completed",
                            "side_effect": "write",
                        }
                    ]
                },
            }
        ],
        run_id="run_policy_tx",
    )

    evaluations = evaluate_policy_signals(signals)

    assert evaluations[0].policy_id == "side_effect_transaction_required"
    assert evaluations[0].selected_action == "rollback"


def test_budget_and_user_control_signals_map_to_observe_actions() -> None:
    evaluations = evaluate_policy_signals(
        [
            PolicySignal(
                signal_id="sig_budget",
                signal_class="usage_budget",
                reason="cost_budget_threshold",
            ),
            PolicySignal(
                signal_id="sig_control",
                signal_class="user_control",
                reason="user_control_interrupt",
            ),
        ],
        profile=HarnessPolicyProfile(profile_id="test", mode="observe"),
    )

    actions_by_policy = {item.policy_id: item.selected_action for item in evaluations}
    assert actions_by_policy["budget_threshold"] == "force_final"
    assert actions_by_policy["user_steering_interrupt"] == "interrupt_for_approval"


def test_policy_action_contract_serializes_action_vocabulary() -> None:
    action = PolicyAction(
        action="ask_user",
        reason="tool_loop_no_progress_would_ask_or_stop",
        affected_tools=["web_fetch"],
        retry_budget=0,
    )

    assert action.model_dump(mode="json")["action"] == "ask_user"


@pytest.mark.parametrize(
    "action",
    [
        "continue",
        "warn",
        "retry",
        "compact",
        "switch_provider_route",
        "reshape_request",
        "force_final",
        "ask_user",
        "interrupt_for_approval",
        "block_tool",
        "rollback",
        "abort",
        "mark_achieved",
        "mark_blocked",
        "fail_fast",
    ],
)
def test_policy_action_contract_accepts_full_action_vocabulary(action: str) -> None:
    assert PolicyAction(action=action, reason="contract_coverage").action == action


@pytest.mark.parametrize(
    "signal_class",
    [
        "provider_preflight",
        "provider_error",
        "runtime_decision",
        "tool_guardrail",
        "provenance_contract",
        "lifecycle",
        "goal_state",
        "usage_budget",
        "user_control",
        "host_metadata",
    ],
)
def test_policy_signal_contract_accepts_full_signal_class_vocabulary(
    signal_class: str,
) -> None:
    signal = PolicySignal(
        signal_id=f"sig_{signal_class}",
        signal_class=signal_class,
        reason="contract_coverage",
    )

    assert signal.signal_class == signal_class


def test_no_policy_match_evaluates_to_continue() -> None:
    evaluations = evaluate_policy_signals(
        [
            PolicySignal(
                signal_id="sig_lifecycle",
                signal_class="lifecycle",
                reason="heartbeat_observed",
            )
        ]
    )

    assert evaluations[0].status == "not_matched"
    assert evaluations[0].selected_action == "continue"
