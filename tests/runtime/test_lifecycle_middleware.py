"""Runtime lifecycle middleware audit executor tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from agent_driver.contracts.hooks import HookResponse
from agent_driver.contracts.lifecycle_hooks import (
    LifecycleHookAuditStatus,
    LifecycleHookEvent,
    LifecycleHookEventType,
    LifecycleHookFailurePolicy,
    LifecycleHookMode,
    LifecycleHookRegistration,
    LifecycleHookVerdict,
    LifecycleMiddlewareChain,
)
from agent_driver.runtime.lifecycle_hooks import RevisionRequest
from agent_driver.runtime.lifecycle_middleware import (
    LifecycleMiddlewareAuditExecutor,
    requires_guardrails_after_transform,
    result_from_existing_hook_output,
)


@dataclass
class _ToolCall:
    tool_call_id: str
    name: str


def _event() -> LifecycleHookEvent:
    return LifecycleHookEvent(
        event_id="evt_1",
        event_type=LifecycleHookEventType.PRE_TOOL_USE,
        run_id="run_1",
        attempt_id="attempt_1",
        seq=1,
    )


def _registration(
    *,
    mode: LifecycleHookMode = LifecycleHookMode.OBSERVE,
    timeout_seconds: float | None = None,
) -> LifecycleHookRegistration:
    return LifecycleHookRegistration(
        hook_id="hook_1",
        owner="tests",
        event_subscriptions=[LifecycleHookEventType.PRE_TOOL_USE],
        timeout_seconds=timeout_seconds,
        mode=mode,
        compatibility_metadata={"declared": True},
    )


@pytest.mark.asyncio
async def test_audit_executor_records_started_and_completed_transform() -> None:
    registration = _registration()
    executor = LifecycleMiddlewareAuditExecutor([registration])
    original = _ToolCall(tool_call_id="tc1", name="read")
    replacement = _ToolCall(tool_call_id="tc2", name="read")

    execution = await executor.execute(
        registration,
        _event(),
        lambda: HookResponse(
            value=replacement,
            prevent_continuation=True,
            additional_context={"decision": "redacted"},
        ),
        original_value=original,
    )

    assert execution.value is replacement
    assert execution.result.verdict == LifecycleHookVerdict.TRANSFORM
    assert execution.result.prevent_continuation is True
    assert requires_guardrails_after_transform(execution.result) is True
    assert [row.status for row in execution.audit_records] == [
        LifecycleHookAuditStatus.STARTED,
        LifecycleHookAuditStatus.COMPLETED,
    ]
    assert executor.audit_records[-1].result.control_metadata == {
        "additional_context": {"decision": "redacted"}
    }


@pytest.mark.asyncio
async def test_observe_hook_failure_does_not_block_run() -> None:
    registration = _registration(mode=LifecycleHookMode.OBSERVE)
    executor = LifecycleMiddlewareAuditExecutor(
        [registration],
        chain=LifecycleMiddlewareChain(
            chain_id="chain",
            registration_ids=["hook_1"],
            failure_policy=LifecycleHookFailurePolicy.BLOCK_IF_ENFORCE,
        ),
    )

    async def fail():
        raise RuntimeError("boom")

    execution = await executor.execute(
        registration, _event(), fail, original_value="ok"
    )

    assert execution.value == "ok"
    assert execution.result.verdict == LifecycleHookVerdict.ERROR
    assert execution.result.continuation_behavior == "continue"
    assert execution.audit_records[-1].status == LifecycleHookAuditStatus.FAILED


@pytest.mark.asyncio
async def test_enforce_hook_blocks_only_when_policy_configured() -> None:
    registration = _registration(mode=LifecycleHookMode.ENFORCE)

    async def fail():
        raise RuntimeError("boom")

    continue_executor = LifecycleMiddlewareAuditExecutor(
        [registration],
        chain=LifecycleMiddlewareChain(
            chain_id="continue",
            registration_ids=["hook_1"],
            failure_policy=LifecycleHookFailurePolicy.CONTINUE,
        ),
    )
    continued = await continue_executor.execute(
        registration, _event(), fail, original_value="ok"
    )
    assert continued.result.verdict == LifecycleHookVerdict.ERROR
    assert continued.result.continuation_behavior == "continue"

    block_executor = LifecycleMiddlewareAuditExecutor(
        [registration],
        chain=LifecycleMiddlewareChain(
            chain_id="block",
            registration_ids=["hook_1"],
            failure_policy=LifecycleHookFailurePolicy.BLOCK_IF_ENFORCE,
        ),
    )
    blocked = await block_executor.execute(
        registration, _event(), fail, original_value="ok"
    )
    assert blocked.result.verdict == LifecycleHookVerdict.BLOCK
    assert blocked.result.continuation_behavior == "block"
    assert blocked.audit_records[-1].status == LifecycleHookAuditStatus.BLOCKED


@pytest.mark.asyncio
async def test_timeout_records_timed_out_row() -> None:
    registration = _registration(timeout_seconds=0.001)
    executor = LifecycleMiddlewareAuditExecutor([registration])

    async def slow():
        await asyncio.sleep(0.05)

    execution = await executor.execute(
        registration, _event(), slow, original_value="ok"
    )

    assert execution.value == "ok"
    assert execution.result.verdict == LifecycleHookVerdict.TIMEOUT
    assert execution.result.timed_out is True
    assert execution.audit_records[-1].status == LifecycleHookAuditStatus.TIMED_OUT


def test_existing_lifecycle_outputs_bridge_to_typed_verdicts() -> None:
    revision = RevisionRequest(feedback="try again")
    _value, result = result_from_existing_hook_output("hook_1", revision)

    assert result.verdict == LifecycleHookVerdict.REQUEST_REVISION
    assert result.action_metadata == {"revision_requested": True}
