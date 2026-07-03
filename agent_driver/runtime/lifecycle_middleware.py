"""Deterministic lifecycle hook audit executor.

The executor records a contract-level audit trail around existing hook calls.
It does not replace ``ToolHook``, ``RunLifecycleHook`` or ``HookChainExecutor``;
it gives hosts a small wrapper they can adopt incrementally.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_driver.contracts.hooks import HookResponse
from agent_driver.contracts.lifecycle_hooks import (
    LifecycleHookAuditRecord,
    LifecycleHookAuditStatus,
    LifecycleHookEvent,
    LifecycleHookFailurePolicy,
    LifecycleHookMode,
    LifecycleHookRegistration,
    LifecycleHookResult,
    LifecycleHookVerdict,
    LifecycleMiddlewareChain,
)

HookCallable = Callable[[], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class LifecycleHookExecution:
    """Result of one audited hook invocation."""

    value: Any
    result: LifecycleHookResult
    audit_records: list[LifecycleHookAuditRecord]


class LifecycleMiddlewareAuditExecutor:
    """Audit wrapper for lifecycle middleware calls."""

    def __init__(
        self,
        registrations: Iterable[LifecycleHookRegistration] = (),
        *,
        chain: LifecycleMiddlewareChain | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._registrations = {
            registration.hook_id: registration
            for registration in sorted(registrations, key=lambda item: item.order)
        }
        self._chain = chain or LifecycleMiddlewareChain(
            chain_id="default_lifecycle_middleware",
            registration_ids=list(self._registrations),
        )
        self._now = now or time.perf_counter
        self.audit_records: list[LifecycleHookAuditRecord] = []

    async def execute(
        self,
        registration: LifecycleHookRegistration,
        event: LifecycleHookEvent,
        callback: HookCallable,
        *,
        original_value: Any = None,
    ) -> LifecycleHookExecution:
        """Invoke a hook callback and record deterministic audit rows."""
        if registration.mode == LifecycleHookMode.DISABLED:
            result = LifecycleHookResult(
                hook_id=registration.hook_id,
                verdict=LifecycleHookVerdict.SKIP,
                continuation_behavior="continue",
            )
            skipped = self._record(
                event,
                result,
                LifecycleHookAuditStatus.SKIPPED,
                skipped_reason="hook_registration_disabled",
            )
            return LifecycleHookExecution(original_value, result, [skipped])

        started = self._record(
            event,
            LifecycleHookResult(
                hook_id=registration.hook_id,
                verdict=LifecycleHookVerdict.OBSERVE,
                continuation_behavior="running",
            ),
            LifecycleHookAuditStatus.STARTED,
        )
        start = self._now()
        timeout = self._timeout_for(registration)
        try:
            raw = await self._await_callback(callback, timeout=timeout)
        except TimeoutError:
            elapsed = self._elapsed_ms(start)
            result = self._failure_result(
                registration,
                verdict=LifecycleHookVerdict.TIMEOUT,
                error_class="TimeoutError",
                elapsed_ms=elapsed,
                timed_out=True,
            )
            status = self._status_for_result(result)
            final = self._record(event, result, status)
            return LifecycleHookExecution(original_value, result, [started, final])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            elapsed = self._elapsed_ms(start)
            result = self._failure_result(
                registration,
                verdict=LifecycleHookVerdict.ERROR,
                error_class=type(exc).__name__,
                elapsed_ms=elapsed,
            )
            status = self._status_for_result(result)
            final = self._record(event, result, status)
            return LifecycleHookExecution(original_value, result, [started, final])

        elapsed = self._elapsed_ms(start)
        value, result = result_from_existing_hook_output(
            registration.hook_id,
            raw,
            original_value=original_value,
            elapsed_ms=elapsed,
        )
        final = self._record(event, result, self._status_for_result(result))
        return LifecycleHookExecution(value, result, [started, final])

    def compatibility_report_records(self) -> list[LifecycleHookAuditRecord]:
        """Return audit records in emission order."""
        return list(self.audit_records)

    async def _await_callback(
        self, callback: HookCallable, *, timeout: float | None
    ) -> Any:
        value = callback()
        if inspect.isawaitable(value):
            if timeout is not None:
                try:
                    return await asyncio.wait_for(value, timeout=timeout)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError from exc
            return await value
        return value

    def _timeout_for(self, registration: LifecycleHookRegistration) -> float | None:
        return registration.timeout_seconds or self._chain.timeout_default_seconds

    def _elapsed_ms(self, start: float) -> float:
        return round(max(0.0, self._now() - start) * 1000, 3)

    def _failure_result(
        self,
        registration: LifecycleHookRegistration,
        *,
        verdict: LifecycleHookVerdict,
        error_class: str,
        elapsed_ms: float,
        timed_out: bool = False,
    ) -> LifecycleHookResult:
        continuation = "continue"
        effective_verdict = verdict
        if self._chain.failure_policy == LifecycleHookFailurePolicy.SKIP_REMAINING:
            continuation = "skip_remaining"
        elif self._chain.failure_policy == LifecycleHookFailurePolicy.FAIL_RUN:
            continuation = "fail_run"
        elif (
            self._chain.failure_policy == LifecycleHookFailurePolicy.BLOCK_IF_ENFORCE
            and registration.mode == LifecycleHookMode.ENFORCE
        ):
            effective_verdict = LifecycleHookVerdict.BLOCK
            continuation = "block"
        return LifecycleHookResult(
            hook_id=registration.hook_id,
            verdict=effective_verdict,
            elapsed_ms=elapsed_ms,
            timed_out=timed_out,
            error_class=error_class,
            continuation_behavior=continuation,
        )

    @staticmethod
    def _status_for_result(result: LifecycleHookResult) -> LifecycleHookAuditStatus:
        if result.verdict == LifecycleHookVerdict.BLOCK:
            return LifecycleHookAuditStatus.BLOCKED
        if result.verdict == LifecycleHookVerdict.TIMEOUT or result.timed_out:
            return LifecycleHookAuditStatus.TIMED_OUT
        if result.verdict == LifecycleHookVerdict.ERROR:
            return LifecycleHookAuditStatus.FAILED
        if result.verdict == LifecycleHookVerdict.NO_CLAIM:
            return LifecycleHookAuditStatus.NO_CLAIM
        if result.verdict == LifecycleHookVerdict.SKIP:
            return LifecycleHookAuditStatus.SKIPPED
        return LifecycleHookAuditStatus.COMPLETED

    def _record(
        self,
        event: LifecycleHookEvent,
        result: LifecycleHookResult,
        status: LifecycleHookAuditStatus,
        *,
        skipped_reason: str | None = None,
        no_claim_reason: str | None = None,
    ) -> LifecycleHookAuditRecord:
        record = LifecycleHookAuditRecord(
            audit_id=f"{event.event_id}:{result.hook_id}:{len(self.audit_records) + 1}",
            event=event,
            result=result,
            status=status,
            artifact_refs=list(event.artifact_refs),
            skipped_reason=skipped_reason,
            no_claim_reason=no_claim_reason,
            created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        )
        self.audit_records.append(record)
        return record


def result_from_existing_hook_output(
    hook_id: str,
    value: Any,
    *,
    original_value: Any = None,
    elapsed_ms: float | None = None,
) -> tuple[Any, LifecycleHookResult]:
    """Bridge existing hook return shapes into a lifecycle result."""
    prevent_continuation = False
    control_metadata: dict[str, Any] = {}
    raw_value = value
    if isinstance(value, HookResponse):
        prevent_continuation = value.prevent_continuation
        control_metadata = {
            "additional_context": value.additional_context,
        }
        raw_value = value.value

    verdict = _verdict_for_value(raw_value, original_value=original_value)
    if raw_value is None:
        output_value = original_value
    else:
        output_value = raw_value

    continuation = "skip_remaining" if prevent_continuation else "continue"
    result = LifecycleHookResult(
        hook_id=hook_id,
        verdict=verdict,
        transformed_value_summary=(
            _summary_for_value(output_value)
            if verdict == LifecycleHookVerdict.TRANSFORM
            else None
        ),
        control_metadata=control_metadata,
        action_metadata=_action_metadata_for_value(raw_value),
        elapsed_ms=elapsed_ms,
        prevent_continuation=prevent_continuation,
        continuation_behavior=continuation,
    )
    return output_value, result


def requires_guardrails_after_transform(result: LifecycleHookResult) -> bool:
    """Return true when a hook transform must be rechecked by guardrails."""
    return result.verdict == LifecycleHookVerdict.TRANSFORM


def _verdict_for_value(value: Any, *, original_value: Any) -> LifecycleHookVerdict:
    if value is None:
        return LifecycleHookVerdict.OBSERVE
    class_name = type(value).__name__
    if class_name == "RevisionRequest":
        return LifecycleHookVerdict.REQUEST_REVISION
    if class_name == "FinalizeNow":
        return LifecycleHookVerdict.FINALIZE
    if class_name == "FallbackSpec":
        return LifecycleHookVerdict.SPAWN_FALLBACK
    if _looks_like_approval_request(value):
        return LifecycleHookVerdict.REQUEST_APPROVAL
    if original_value is not None and value is not original_value:
        return LifecycleHookVerdict.TRANSFORM
    return LifecycleHookVerdict.OBSERVE


def _action_metadata_for_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    class_name = type(value).__name__
    if class_name == "RevisionRequest":
        return {"revision_requested": True}
    if class_name == "FinalizeNow":
        return {"finalize_now": True}
    if class_name == "FallbackSpec":
        return {
            "fallback_rule": getattr(value, "rule_name", None),
            "triggered_by": getattr(value, "triggered_by", None),
        }
    if _looks_like_approval_request(value):
        return {"approval_requested": True}
    return {}


def _looks_like_approval_request(value: Any) -> bool:
    return all(hasattr(value, attr) for attr in ("request_id", "response_options"))


def _summary_for_value(value: Any) -> str:
    name = type(value).__name__
    stable_id = (
        getattr(value, "tool_call_id", None)
        or getattr(value, "id", None)
        or getattr(value, "name", None)
    )
    if stable_id:
        return f"{name}:{stable_id}"
    return name


__all__ = [
    "LifecycleHookExecution",
    "LifecycleMiddlewareAuditExecutor",
    "requires_guardrails_after_transform",
    "result_from_existing_hook_output",
]
