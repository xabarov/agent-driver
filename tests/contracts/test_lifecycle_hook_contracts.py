"""Lifecycle hook middleware contract tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts.lifecycle_hooks import (
    LifecycleHookAuditRecord,
    LifecycleHookAuditStatus,
    LifecycleHookCompatibilityReport,
    LifecycleHookEvent,
    LifecycleHookEventType,
    LifecycleHookFailurePolicy,
    LifecycleHookMode,
    LifecycleHookRegistration,
    LifecycleHookResult,
    LifecycleHookVerdict,
    LifecycleMiddlewareChain,
)


def test_lifecycle_hook_contracts_accept_redacted_json_shapes() -> None:
    event = LifecycleHookEvent(
        event_id="evt_1",
        event_type=LifecycleHookEventType.PRE_TOOL_USE,
        run_id="run_1",
        seq=1,
        redacted_metadata={"api_key": "OPENROUTER_API_KEY", "summary": "safe"},
    )
    result = LifecycleHookResult(
        hook_id="hook_1",
        verdict=LifecycleHookVerdict.TRANSFORM,
        transformed_value_summary="ToolCall:tc1",
        elapsed_ms=1.5,
    )
    record = LifecycleHookAuditRecord(
        audit_id="audit_1",
        event=event,
        result=result,
        status=LifecycleHookAuditStatus.COMPLETED,
    )

    assert record.event.event_type == LifecycleHookEventType.PRE_TOOL_USE
    assert record.result.verdict == LifecycleHookVerdict.TRANSFORM


def test_lifecycle_hook_contracts_reject_secret_values_and_non_json() -> None:
    with pytest.raises(ValueError, match="must not contain secret"):
        LifecycleHookEvent(
            event_id="evt_1",
            event_type=LifecycleHookEventType.RUN_START,
            redacted_metadata={"api_key": "sk-live-value"},
        )

    with pytest.raises(ValueError, match="must not contain secret-shaped"):
        LifecycleHookResult(
            hook_id="hook_1",
            warning_metadata={"value": "sk-abcdefghijklmnop"},
        )

    with pytest.raises(ValueError, match="JSON-serializable"):
        LifecycleHookResult(
            hook_id="hook_1",
            control_metadata={"bad": object()},
        )


def test_lifecycle_hook_registration_validates_required_fields() -> None:
    with pytest.raises(ValueError, match="event_subscriptions"):
        LifecycleHookRegistration(
            hook_id="hook_1",
            owner="owner",
            event_subscriptions=[],
        )

    with pytest.raises(ValueError, match="event_subscriptions"):
        LifecycleHookRegistration(
            hook_id="hook_1",
            owner="owner",
            event_subscriptions=[
                LifecycleHookEventType.RUN_START,
                LifecycleHookEventType.RUN_START,
            ],
        )

    with pytest.raises(ValueError, match="side effects"):
        LifecycleHookRegistration(
            hook_id="hook_1",
            owner="owner",
            event_subscriptions=[LifecycleHookEventType.FILE_CHANGED],
            mode=LifecycleHookMode.ENFORCE,
            side_effect_permissions=["write"],
        )


def test_lifecycle_middleware_chain_validates_ordering_budget() -> None:
    chain = LifecycleMiddlewareChain(
        chain_id="chain_1",
        registration_ids=["a", "b"],
        failure_policy=LifecycleHookFailurePolicy.BLOCK_IF_ENFORCE,
        max_hook_count=2,
    )

    assert chain.registration_ids == ["a", "b"]
    assert chain.failure_policy == LifecycleHookFailurePolicy.BLOCK_IF_ENFORCE

    with pytest.raises(ValueError, match="unique"):
        LifecycleMiddlewareChain(chain_id="bad", registration_ids=["a", "a"])


def test_lifecycle_compatibility_report_rejects_unknown_statuses() -> None:
    registration = LifecycleHookRegistration(
        hook_id="hook_1",
        owner="owner",
        event_subscriptions=[LifecycleHookEventType.RUN_START],
    )

    report = LifecycleHookCompatibilityReport(
        report_id="report_1",
        generated_at="2026-07-03T00:00:00Z",
        product_family="chat_demo",
        supported_events={LifecycleHookEventType.RUN_START.value: "supported"},
        registrations=[registration],
        modes_active={"hook_1": "observe"},
    )
    assert report.audit_record_count == 0

    with pytest.raises(ValueError, match="unsupported lifecycle event status"):
        LifecycleHookCompatibilityReport(
            report_id="report_1",
            generated_at="2026-07-03T00:00:00Z",
            product_family="chat_demo",
            supported_events={LifecycleHookEventType.RUN_START.value: "maybe"},
        )


def test_lifecycle_hook_result_rejects_invalid_timeout_shape() -> None:
    with pytest.raises(ValueError, match="timeout or block verdict"):
        LifecycleHookResult(
            hook_id="hook_1",
            verdict=LifecycleHookVerdict.ERROR,
            timed_out=True,
            error_class="TimeoutError",
        )
