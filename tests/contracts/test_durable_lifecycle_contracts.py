"""Durable lifecycle contract tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts.durable_lifecycle import (
    AttachPlan,
    BackgroundRunLease,
    DurableCheckpointIndex,
    DurableDurabilityLevel,
    DurableInterruptRecord,
    DurableInterruptStatus,
    DurableLifecycleCompatibilityReport,
    DurableLifecycleStatus,
    DurablePlanVerdict,
    DurableRunRecord,
    DurableSessionRecord,
    DurableSideEffectSafety,
    ForkPlan,
)


def test_durable_lifecycle_contracts_accept_redacted_json_shapes() -> None:
    session = DurableSessionRecord(
        session_id="session_1",
        adapter_id="chat_demo",
        durability_level=DurableDurabilityLevel.JSONL,
        search_metadata={"api_key": "OPENROUTER_API_KEY", "title": "safe"},
    )
    run = DurableRunRecord(
        run_id="run_1",
        session_id=session.session_id,
        status=DurableLifecycleStatus.PAUSED,
        latest_seq=2,
        reconnect_cursor="run_1:2",
        latest_checkpoint_id="checkpoint_1",
        paused_interrupt_id="interrupt_1",
    )
    checkpoint = DurableCheckpointIndex(
        checkpoint_id="checkpoint_1",
        run_id="run_1",
        graph_id="agent",
        state_version="v1",
        storage_backend=DurableDurabilityLevel.SQLITE,
        resumable=True,
        side_effect_safety=DurableSideEffectSafety.SAFE,
    )

    assert session.durability_level == DurableDurabilityLevel.JSONL
    assert run.reconnect_cursor == "run_1:2"
    assert checkpoint.resumable is True


def test_durable_contracts_reject_secret_values_non_json_and_bad_cursor() -> None:
    with pytest.raises(ValueError, match="must not contain secret"):
        DurableSessionRecord(
            session_id="session_1",
            search_metadata={"api_key": "sk-live-value"},
        )

    with pytest.raises(ValueError, match="secret-shaped"):
        AttachPlan(
            verdict=DurablePlanVerdict.REPLAY_ONLY,
            redacted_metadata={"value": "sk-abcdefghijklmnop"},
        )

    with pytest.raises(ValueError, match="JSON-serializable"):
        BackgroundRunLease(
            lease_id="lease_1",
            run_id="run_1",
            redacted_metadata={"bad": object()},
        )

    with pytest.raises(ValueError, match="reconnect_cursor"):
        DurableRunRecord(
            run_id="run_1",
            session_id="session_1",
            latest_seq=2,
            reconnect_cursor="wrong",
        )


def test_durable_interrupts_require_resolution_timestamps() -> None:
    with pytest.raises(ValueError, match="resolved_at"):
        DurableInterruptRecord(
            interrupt_id="interrupt_1",
            run_id="run_1",
            reason="approval",
            status=DurableInterruptStatus.RESOLVED,
            resolution={"action": "approve"},
        )


def test_fork_plan_available_requires_new_ids() -> None:
    with pytest.raises(ValueError, match="new ids"):
        ForkPlan(verdict=DurablePlanVerdict.FORK_AVAILABLE)


def test_durable_compatibility_report_validates_statuses() -> None:
    report = DurableLifecycleCompatibilityReport(
        report_id="report_1",
        generated_at="2026-07-03T00:00:00Z",
        product_family="chat_demo",
        feature_statuses={"resume_plan": "no_claim"},
    )
    assert report.feature_statuses["resume_plan"] == "no_claim"

    with pytest.raises(ValueError, match="unsupported durable lifecycle status"):
        DurableLifecycleCompatibilityReport(
            report_id="report_1",
            generated_at="2026-07-03T00:00:00Z",
            product_family="chat_demo",
            feature_statuses={"resume_plan": "maybe"},
        )
