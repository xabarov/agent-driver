"""Allow-path tool execution: guardrails, handler, output budgets, final guard."""

from __future__ import annotations

from collections.abc import Callable
import json
import re
from typing import Any

from agent_driver.contracts.context import PlanApprovalPayload
from agent_driver.contracts.enums import (
    GuardrailDecision,
    InterruptReason,
    ResumeAction,
    ToolPolicyDecision,
)
from agent_driver.contracts.interrupts import AllowedPrompt, InterruptRequest
from agent_driver.contracts.tools import (
    MANAGEMENT_TOOL_NAMES,
    ToolError,
    ToolManifest,
    ToolResultEnvelope,
)
from agent_driver.runtime.planning_check import is_exit_plan_mode_tool
from agent_driver.tools.cancellation import ToolCancellation
from agent_driver.tools.context import (
    tool_call_context_scope,
    tool_cancellation_scope,
    tool_progress_scope,
)
from agent_driver.tools.executor.blocks import append_blocked_call
from agent_driver.tools.executor.interrupt_ids import (
    build_attempt_id,
    build_interrupt_id,
)
from agent_driver.tools.executor.specs import (
    AllowedSpec,
    BlockSpec,
    merge_guardrail_decisions,
)
from agent_driver.tools.executor.spill import (
    should_spill_payload,
    spill_payload_to_artifact,
)
from agent_driver.tools.executor.trace import (
    build_tool_trace,
    trace_spec_completed,
    trace_spec_denied,
    trace_spec_failed,
)
from agent_driver.tools.guardrails import GuardrailPipeline, enforce_output_budget


def _bounded_structured_output(
    raw: Any,
    *,
    max_chars: int | None,
) -> tuple[dict[str, Any] | Any, bool]:
    """Best-effort bound for large structured outputs."""
    if not isinstance(raw, dict) or max_chars is None or max_chars <= 0:
        return raw, False
    encoded = json.dumps(raw, ensure_ascii=True)
    if len(encoded) <= max_chars:
        return raw, False
    payload = dict(raw)
    omitted_count = 0
    for key in ("results", "matches", "tools", "skills", "symbols"):
        value = payload.get(key)
        if isinstance(value, list) and len(value) > 20:
            omitted_count = max(0, len(value) - 20)
            payload[key] = value[:20]
            payload["truncated"] = True
            payload["limit"] = "output_char_budget"
            payload["limit_value"] = max_chars
            payload["omitted_count"] = omitted_count
            return payload, True
    content = payload.get("content")
    if isinstance(content, str) and len(content) > max_chars:
        omitted_count = len(content) - max_chars
        payload["content"] = content[:max_chars]
        payload["truncated"] = True
        payload["limit"] = "output_char_budget"
        payload["limit_value"] = max_chars
        payload["omitted_count"] = omitted_count
        return payload, True
    return payload, False


def _raw_summary_candidate(raw: Any) -> str | None:
    """Extract the best compact tool summary from common handler fields."""
    if not isinstance(raw, dict):
        return None
    for key in ("summary", "result_summary", "observation"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for key in ("output_preview", "content"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= 2000:
            return value
    return None


def _planning_update_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize planning tool output payload for runtime state updates."""
    applied_args = raw.get("applied_args")
    if not isinstance(applied_args, dict):
        applied_args = {}
    return {
        "summary": raw.get("summary", "planning updated"),
        "applied_args": applied_args,
        "planning_step": raw.get("planning_step"),
        "planning_state": raw.get("planning_state"),
    }


def _interrupt_identifiers(spec: AllowedSpec) -> tuple[str, str]:
    run_id = str(spec.run_metadata.get("run_id") or "run_pending")
    attempt_id = build_attempt_id(
        index=spec.index, attempt_id=spec.run_metadata.get("attempt_id")
    )
    return run_id, attempt_id


def _success_field_failure(
    *, manifest: ToolManifest, raw: Any
) -> tuple[str, str] | None:
    """Detect an opt-in self-reported tool failure.

    Returns ``(message, error_code)`` when the manifest declares
    ``success_field`` and the structured output carries that field with a
    falsy value; otherwise ``None`` (field absent or truthy → COMPLETED).
    A missing field never forces a false FAILED.
    """
    field = manifest.success_field
    if not field or not isinstance(raw, dict) or field not in raw:
        return None
    if raw.get(field):
        return None
    error = raw.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "tool_reported_failure")
        message = str(error.get("message") or error)
    elif error:
        code = "tool_reported_failure"
        message = str(error)
    else:
        code = "tool_reported_failure"
        message = f"{manifest.name} reported {field}=False"
    return message, code


def _append_tool_handler_exception(*, spec: AllowedSpec, error: Exception) -> None:
    message = str(error).strip() or f"{type(error).__name__}"
    envelope = ToolResultEnvelope(
        call=spec.call,
        decision=ToolPolicyDecision.DENY,
        guardrail_decision=GuardrailDecision.ALLOW,
        error=ToolError(code="tool_handler_error", message=message),
        metadata={"error_type": type(error).__name__, **spec.run_metadata},
    )
    trace = build_tool_trace(
        trace_spec_denied(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            summary=message,
            error_code="tool_handler_error",
        )
    )
    spec.result.append(envelope=envelope, trace=trace)


def _make_progress_recorder(spec: AllowedSpec) -> Callable[[Any], None]:
    """Per-call progress reporter that records each ``report_tool_progress``
    invocation into the executor result (Phase 11 H16). Tools that never call the
    reporter incur no overhead."""

    def _record_progress(progress) -> None:  # noqa: ANN001
        spec.result.record_progress(
            call_index=spec.index,
            tool_name=spec.call.tool_name,
            progress=progress,
            tool_call_id=spec.call.tool_call_id,
        )

    return _record_progress


def _build_cancellation(spec: AllowedSpec) -> ToolCancellation | None:
    """Cooperative cancellation signal exposed to the handler so it can stop its
    own external work when the run is aborted (U4). ``None`` — and no overhead —
    when no abort handle was plumbed into the executor."""
    if spec.cancelled_check is None:
        return None
    raw_run_id = spec.run_metadata.get("run_id")
    raw_attempt = spec.run_metadata.get("attempt_id")
    return ToolCancellation(
        run_id=str(raw_run_id) if raw_run_id is not None else None,
        tool_call_id=spec.call.tool_call_id,
        attempt_id=(
            str(raw_attempt) if raw_attempt is not None else f"attempt_{spec.index}"
        ),
        deadline_seconds=spec.cancellation_deadline,
        _check=spec.cancelled_check,
    )


def _allowed_precheck_blocked(spec: AllowedSpec, *, args_guard: Any) -> bool:
    """Pre-execution gates for the allow path: tool-args guardrail block, unregistered
    tool (with fuzzy-match feedback), a deferred blind call missing required args, and a
    pre-start abort. Appends the blocked envelope and returns True when the call must not
    execute; False to proceed to the handler."""
    if args_guard.decision == GuardrailDecision.BLOCK:
        append_blocked_call(
            result=spec.result,
            spec=BlockSpec(
                index=spec.index,
                call=spec.call,
                manifest=spec.manifest,
                reason=args_guard.reason or "guardrail blocked tool args",
                code="guardrail_blocked",
                stage="tool_args",
            ),
        )
        return True
    if spec.registered is None:
        # Phase 13 H29.3 — enrich the feedback string with closest-match
        # suggestions when the executor knows the registry's tool names.
        # Models (especially open-weights) often recover on the next
        # turn when shown a fuzzy match for their misspelled call.
        if spec.available_tool_names:
            from agent_driver.tools.fallback_feedback import (
                build_unknown_tool_feedback,
            )

            reason = build_unknown_tool_feedback(
                spec.call.tool_name, spec.available_tool_names
            )
        else:
            reason = "tool is not registered"
        append_blocked_call(
            result=spec.result,
            spec=BlockSpec(
                index=spec.index,
                call=spec.call,
                manifest=spec.manifest,
                code="tool_not_registered",
                reason=reason,
            ),
        )
        return True
    if _deferred_blind_call_missing_required(spec):
        return True
    # U4 — prevent new work once an abort was observed: if the run was already
    # aborted before this handler starts, skip execution and record a blocked
    # envelope instead of launching (possibly external) side-effecting work.
    if spec.cancelled_check is not None and spec.cancelled_check():
        append_blocked_call(
            result=spec.result,
            spec=BlockSpec(
                index=spec.index,
                call=spec.call,
                manifest=spec.manifest,
                code="run_aborted",
                reason="run aborted before tool execution",
            ),
        )
        return True
    return False


async def _finalize_allowed_envelope(
    guardrails: GuardrailPipeline,
    spec: AllowedSpec,
    *,
    raw: Any,
    bounded_summary: str | None,
    truncated: bool,
    structured_truncated: bool,
    args_guard_decision: Any,
    raw_guard_decision: Any,
) -> tuple[ToolResultEnvelope, Any]:
    """Build the ALLOW result envelope from the bounded handler output, run the
    final-output guardrail, and produce the matching trace: a guardrail block yields a
    DENY envelope + denied trace; otherwise a self-reported success-field failure yields
    a FAILED trace, and a clean run a COMPLETED trace."""
    envelope = ToolResultEnvelope(
        call=spec.call,
        decision=ToolPolicyDecision.ALLOW,
        guardrail_decision=merge_guardrail_decisions(
            spec.input_guard_decision,
            args_guard_decision,
            raw_guard_decision,
        ),
        summary=bounded_summary,
        structured_output=raw,
        truncated=truncated or structured_truncated,
        metadata={
            "idempotent": spec.manifest.idempotent,
            **spec.run_metadata,
        },
    )
    final_guard = await guardrails.on_final_output(envelope.model_dump(mode="json"))
    if final_guard.decision == GuardrailDecision.BLOCK:
        envelope = ToolResultEnvelope(
            call=spec.call,
            decision=ToolPolicyDecision.DENY,
            guardrail_decision=final_guard.decision,
            error=ToolError(
                code="guardrail_blocked",
                message=final_guard.reason or "guardrail blocked final output",
            ),
            metadata={
                "guardrail_stage": "final_output",
                **spec.run_metadata,
            },
        )
        trace = build_tool_trace(
            trace_spec_denied(
                index=spec.index,
                call=spec.call,
                manifest=spec.manifest,
                error_code="guardrail_blocked",
            )
        )
        return envelope, trace
    envelope = envelope.model_copy(
        update={
            "guardrail_decision": merge_guardrail_decisions(
                envelope.guardrail_decision,
                final_guard.decision,
            )
        }
    )
    failure = _success_field_failure(manifest=spec.manifest, raw=raw)
    if failure is not None:
        # The tool ran and was policy-allowed, but self-reported
        # failure. Keep decision=ALLOW (it executed) yet make the
        # failure honest: FAILED trace + error on the envelope, so
        # no consumer has to re-classify status downstream.
        message, error_code = failure
        envelope = envelope.model_copy(
            update={"error": ToolError(code=error_code, message=message)}
        )
        trace = build_tool_trace(
            trace_spec_failed(
                index=spec.index,
                call=spec.call,
                manifest=spec.manifest,
                summary=message,
                error_code=error_code,
                truncated=envelope.truncated,
            )
        )
        return envelope, trace
    trace = build_tool_trace(
        trace_spec_completed(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            summary=envelope.summary,
            truncated=envelope.truncated,
        )
    )
    return envelope, trace


async def execute_allowed_path(
    *,
    guardrails: GuardrailPipeline,
    spec: AllowedSpec,
) -> bool:
    """Execute allow-path flow including guardrails and budgets."""
    args_guard = await guardrails.on_tool_args(
        {"tool_name": spec.call.tool_name, "args": spec.call.args}
    )
    if _allowed_precheck_blocked(spec, args_guard=args_guard):
        return False
    # The precheck returns True (handled above) when spec.registered is None, so a
    # registered tool is guaranteed here.
    assert spec.registered is not None
    try:
        with (
            tool_call_context_scope(
                run_id=str(spec.run_metadata.get("run_id") or ""),
                thread_id=str(spec.run_metadata.get("thread_id") or ""),
                tool_call_id=spec.call.tool_call_id,
                attempt_id=str(spec.run_metadata.get("attempt_id") or ""),
            ),
            tool_progress_scope(_make_progress_recorder(spec)),
            tool_cancellation_scope(_build_cancellation(spec)),
        ):
            raw = await spec.registered.handler(spec.call.args)
        raw_guard = await guardrails.on_tool_result(
            {"tool_name": spec.call.tool_name, "result": raw}
        )
        if raw_guard.decision == GuardrailDecision.BLOCK:
            append_blocked_call(
                result=spec.result,
                spec=BlockSpec(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    reason=raw_guard.reason or "guardrail blocked tool result",
                    code="guardrail_blocked",
                    stage="tool_result",
                ),
            )
            return False
        if spec.call.tool_name == "planning_state_update":
            raw = _planning_update_payload(raw if isinstance(raw, dict) else {})
        if spec.call.tool_name == "ask_user_question":
            return _append_clarification_interrupt(spec=spec, raw=raw)
        if spec.call.tool_name == "wait_for_event":
            return _append_wait_for_event_interrupt(spec=spec, raw=raw)
        if is_exit_plan_mode_tool(spec.call.tool_name) and not spec.call.metadata.get(
            "approved_interrupt_id"
        ):
            return _append_plan_approval_interrupt(spec=spec, raw=raw)
        # Phase 12 H18 — disk-spill for oversized handler outputs.
        # When the manifest has ``max_result_size_chars`` set AND the
        # executor has an ArtifactStore wired, persist the full payload
        # to storage and replace the in-context value with a 2 KB
        # preview + artifact reference. Falls back to legacy
        # ``_bounded_structured_output`` truncation when either
        # condition isn't met or when spill fails.
        if should_spill_payload(
            payload=raw,
            max_result_size_chars=spec.manifest.max_result_size_chars,
            store=spec.artifact_store,
        ):
            spilled = spill_payload_to_artifact(
                payload=raw,
                store=spec.artifact_store,
                tool_name=spec.call.tool_name,
                run_id=str(spec.run_metadata.get("run_id") or ""),
                tool_call_id=spec.call.tool_call_id,
            )
            if spilled is not None:
                raw = spilled[0]
        raw, structured_truncated = _bounded_structured_output(
            raw,
            max_chars=spec.manifest.output_char_budget,
        )
        summary = _raw_summary_candidate(raw)
        bounded_summary, truncated = enforce_output_budget(
            summary, spec.manifest.output_char_budget
        )
        envelope, trace = await _finalize_allowed_envelope(
            guardrails,
            spec,
            raw=raw,
            bounded_summary=bounded_summary,
            truncated=truncated,
            structured_truncated=structured_truncated,
            args_guard_decision=args_guard.decision,
            raw_guard_decision=raw_guard.decision,
        )
        spec.result.append(
            envelope=envelope,
            trace=trace,
        )
    except Exception as exc:  # noqa: BLE001 - tool handlers are untrusted.
        _append_tool_handler_exception(spec=spec, error=exc)
    return False


def _append_clarification_interrupt(*, spec: AllowedSpec, raw: dict[str, Any]) -> bool:
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("ask_user_question requires prompt")
    choices = raw.get("choices")
    if not isinstance(choices, list):
        choices = []
    questions = raw.get("questions")
    if not isinstance(questions, list):
        questions = []
    allow_multiple = bool(raw.get("allow_multiple", False))
    run_id, attempt_id = _interrupt_identifiers(spec)
    interrupt = InterruptRequest(
        interrupt_id=build_interrupt_id(
            run_id=run_id, tool_call_id=spec.call.tool_call_id, index=spec.index
        ),
        run_id=run_id,
        attempt_id=attempt_id,
        checkpoint_id="checkpoint_pending",
        reason=InterruptReason.CLARIFICATION_REQUIRED,
        title="User clarification required",
        description=prompt,
        proposed_action={
            "tool_name": spec.call.tool_name,
            "tool_call_id": spec.call.tool_call_id,
            "args": spec.call.args,
            "prompt": prompt,
            "choices": choices,
            "questions": questions,
            "allow_multiple": allow_multiple,
        },
        allowed_actions=[
            ResumeAction.CLARIFY,
            ResumeAction.CANCEL,
        ],
        editable_fields=["message"],
        metadata=dict(spec.run_metadata),
    )
    envelope = ToolResultEnvelope(
        call=spec.call,
        decision=ToolPolicyDecision.INTERRUPT,
        summary=str(raw.get("summary") or "clarification requested"),
        structured_output=raw if isinstance(raw, dict) else {},
        interrupt=interrupt.model_dump(mode="json"),
        metadata=dict(spec.run_metadata),
    )
    trace = build_tool_trace(
        trace_spec_denied(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            summary="clarification requested",
            error_code="clarification_required",
        )
    )
    spec.result.append(envelope=envelope, trace=trace, interrupt=interrupt)
    return True


def _missing_required_args(manifest: Any, args: Any) -> list[str]:
    """Return the schema-``required`` keys absent from ``args`` (key-absence only)."""
    schema = getattr(manifest, "args_schema", None)
    if not isinstance(schema, dict):
        return []
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    provided = args if isinstance(args, dict) else {}
    return [key for key in required if isinstance(key, str) and key not in provided]


def _deferred_blind_call_missing_required(spec: AllowedSpec) -> bool:
    """Blind-call schema probe for a deferred tool (epic 033+).

    A deferred tool is omitted from the prompt schema list and rediscovered via
    ``tool_search``; a model that then calls it WITHOUT seeing the schema often ships
    no required args, and cheap models loop ~30 identical invalid calls until the
    budget dies. When a deferred tool is invoked missing a schema-``required`` key,
    return the schema instead of dispatching blind, so the next turn can retry with
    proper args. Key-absence only, no type checking; fails open on any error.
    """
    try:
        if spec.registered is None or not spec.manifest.is_deferred():
            return False
        missing = _missing_required_args(spec.manifest, spec.call.args)
        if not missing:
            return False
    except Exception:  # pylint: disable=broad-exception-caught - probe must fail open
        return False
    schema = getattr(spec.manifest, "args_schema", None)
    try:
        schema_text = json.dumps(schema, ensure_ascii=True, default=str)[:2000]
    except (TypeError, ValueError):
        schema_text = str(schema)[:2000]
    append_blocked_call(
        result=spec.result,
        spec=BlockSpec(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            code="deferred_tool_schema_probe",
            reason=(
                f"'{spec.call.tool_name}' was called without required argument(s) "
                f"{missing}. It was not executed. Its argument schema is: {schema_text}. "
                "Retry the call with the required arguments."
            ),
        ),
    )
    return True


def _append_wait_for_event_interrupt(*, spec: AllowedSpec, raw: dict[str, Any]) -> bool:
    """Park the run on an external event (epic 045 B); mirrors clarification-interrupt."""
    from agent_driver.contracts.wait_for_event import WaitForEventRequest

    subscription_raw = raw.get("wait_for_event") if isinstance(raw, dict) else None
    if not isinstance(subscription_raw, dict):
        raise ValueError("wait_for_event requires a subscription payload")
    subscription = WaitForEventRequest.model_validate(subscription_raw)
    run_id, attempt_id = _interrupt_identifiers(spec)
    interrupt = InterruptRequest(
        interrupt_id=build_interrupt_id(
            run_id=run_id, tool_call_id=spec.call.tool_call_id, index=spec.index
        ),
        run_id=run_id,
        attempt_id=attempt_id,
        checkpoint_id="checkpoint_pending",
        reason=InterruptReason.WAIT_FOR_EVENT,
        title=f"Waiting for event '{subscription.event_key}'",
        description=subscription.description or f"Parked on '{subscription.event_key}'",
        proposed_action={
            "tool_name": spec.call.tool_name,
            "tool_call_id": spec.call.tool_call_id,
            "wait_for_event": subscription.model_dump(mode="json"),
        },
        # The host delivers the event via CLARIFY (payload) or CANCEL; the wait is
        # bounded by the subscription deadline (epic 045 liveness), never infinite.
        allowed_actions=[ResumeAction.CLARIFY, ResumeAction.CANCEL],
        editable_fields=["message"],
        metadata={
            **dict(spec.run_metadata),
            "wait_for_event_key": subscription.event_key,
            "wait_for_event_deadline_seconds": subscription.deadline_seconds,
        },
    )
    envelope = ToolResultEnvelope(
        call=spec.call,
        decision=ToolPolicyDecision.INTERRUPT,
        summary=str(raw.get("summary") or "waiting for event"),
        structured_output=raw if isinstance(raw, dict) else {},
        interrupt=interrupt.model_dump(mode="json"),
        metadata=dict(spec.run_metadata),
    )
    trace = build_tool_trace(
        trace_spec_denied(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            summary="waiting for event",
            error_code="wait_for_event",
        )
    )
    spec.result.append(envelope=envelope, trace=trace, interrupt=interrupt)
    return True


def _current_execution_tools(spec: AllowedSpec) -> tuple[str, ...]:
    names = spec.effective_tool_names or spec.available_tool_names
    return tuple(
        dict.fromkeys(
            name
            for name in (str(value).strip() for value in names)
            if name and name not in MANAGEMENT_TOOL_NAMES
        )
    )


def _append_plan_requested_tools_block(
    *,
    spec: AllowedSpec,
    requested_tools: list[str],
    current_tools: tuple[str, ...],
) -> None:
    invalid = [tool for tool in requested_tools if tool not in set(current_tools)]
    current_text = ", ".join(current_tools) if current_tools else "none"
    invalid_text = ", ".join(invalid) if invalid else "none"
    append_blocked_call(
        result=spec.result,
        spec=BlockSpec(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            code="plan_requested_tools_unavailable",
            reason=(
                "plan requested unavailable tools: "
                f"{invalid_text}; current executable tools: {current_text}"
            ),
            structured_output={
                "error_kind": "plan_requested_tools_unavailable",
                "invalid_requested_tools": invalid,
                "requested_tools": requested_tools,
                "current_executable_tools": list(current_tools),
                "retry_expected": True,
                "remediation": (
                    "Do not request approval for tools that are not executable "
                    "in this run. Revise the plan so requested_tools is a "
                    "subset of current_executable_tools, or answer that the "
                    "requested work cannot be run with the current tool surface."
                ),
            },
        ),
    )


def _mentioned_unavailable_plan_tools(
    content: str,
    *,
    current_tools: tuple[str, ...],
    available_tools: tuple[str, ...],
) -> list[str]:
    allowed = set(current_tools) | MANAGEMENT_TOOL_NAMES
    candidates = [
        name
        for name in dict.fromkeys(str(value).strip() for value in available_tools)
        if name and name not in allowed
    ]
    if not candidates:
        return []
    matches: list[tuple[int, str]] = []
    for name in sorted(candidates, key=lambda item: (-len(item), item)):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        )
        match = pattern.search(content)
        if match is not None:
            matches.append((match.start(), name))
    return [name for _, name in sorted(matches, key=lambda item: (item[0], item[1]))]


def _append_plan_content_tools_block(
    *,
    spec: AllowedSpec,
    mentioned_tools: list[str],
    current_tools: tuple[str, ...],
) -> None:
    current_text = ", ".join(current_tools) if current_tools else "none"
    mentioned_text = ", ".join(mentioned_tools) if mentioned_tools else "none"
    append_blocked_call(
        result=spec.result,
        spec=BlockSpec(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            code="plan_content_mentions_unavailable_tools",
            reason=(
                "plan content mentioned unavailable tools: "
                f"{mentioned_text}; current executable tools: {current_text}"
            ),
            structured_output={
                "error_kind": "plan_content_mentions_unavailable_tools",
                "mentioned_unavailable_tools": mentioned_tools,
                "current_executable_tools": list(current_tools),
                "retry_expected": True,
                "remediation": (
                    "Approval-plan content must describe only work the current "
                    "run can execute. Remove unavailable tool names from the "
                    "plan body, or answer that broader work is outside the "
                    "current tool surface."
                ),
            },
        ),
    )


def _append_plan_approval_interrupt(*, spec: AllowedSpec, raw: Any) -> bool:
    """Append plan approval interrupt when approval-exit tool has plan content."""
    if not isinstance(raw, dict):
        return False
    plan_raw = raw.get("plan_approval")
    if not isinstance(plan_raw, dict):
        return False
    content = str(plan_raw.get("content") or "").strip()
    if not content:
        return False
    requested_tools = list(
        dict.fromkeys(
            str(value).strip()
            for value in (plan_raw.get("requested_tools") or [])
            if str(value).strip()
        )
    )
    current_tools = _current_execution_tools(spec)
    if any(tool_name not in set(current_tools) for tool_name in requested_tools):
        _append_plan_requested_tools_block(
            spec=spec,
            requested_tools=requested_tools,
            current_tools=current_tools,
        )
        return True
    mentioned_unavailable_tools = _mentioned_unavailable_plan_tools(
        content,
        current_tools=current_tools,
        available_tools=spec.available_tool_names,
    )
    if mentioned_unavailable_tools:
        _append_plan_content_tools_block(
            spec=spec,
            mentioned_tools=mentioned_unavailable_tools,
            current_tools=current_tools,
        )
        return True
    run_id, attempt_id = _interrupt_identifiers(spec)
    plan_payload = PlanApprovalPayload(
        plan_id=str(
            plan_raw.get("plan_id") or f"plan_{spec.call.tool_call_id or spec.index}"
        ),
        run_id=run_id,
        agent_id=str(spec.run_metadata.get("agent_id") or "agent"),
        content=content,
        content_hash=str(plan_raw.get("content_hash") or ""),
        path=(str(plan_raw.get("path")) if plan_raw.get("path") is not None else None),
        metadata={
            **spec.run_metadata,
            "source_tool": spec.call.tool_name,
            "objective": plan_raw.get("objective"),
            "requested_tools": list(plan_raw.get("requested_tools") or []),
            "target_urls": list(plan_raw.get("target_urls") or []),
            "tool_ids": list(current_tools),
        },
    )
    proposed_prompts = [
        AllowedPrompt(
            category_id=f"plan:{plan_payload.plan_id}:{index}:{tool_name}",
            description=f"Use {tool_name} within the approved plan and host policy.",
            tool_name=tool_name,
        )
        for index, tool_name in enumerate(requested_tools, start=1)
    ]
    interrupt = InterruptRequest(
        interrupt_id=build_interrupt_id(
            run_id=run_id, tool_call_id=spec.call.tool_call_id, index=spec.index
        ),
        run_id=run_id,
        attempt_id=attempt_id,
        checkpoint_id="checkpoint_pending",
        reason=InterruptReason.PLAN_APPROVAL_REQUIRED,
        title=plan_payload.title,
        description=plan_payload.description,
        proposed_action={
            "tool_name": spec.call.tool_name,
            "tool_call_id": spec.call.tool_call_id,
            "args": spec.call.args,
            "plan_approval": plan_payload.model_dump(mode="json"),
        },
        allowed_actions=[
            ResumeAction.APPROVE,
            ResumeAction.REJECT,
            ResumeAction.EDIT,
            ResumeAction.CLARIFY,
            ResumeAction.CANCEL,
        ],
        editable_fields=["content", "path"],
        proposed_prompts=proposed_prompts,
        metadata={
            **spec.run_metadata,
            "plan_id": plan_payload.plan_id,
            "content_hash": plan_payload.content_hash,
            "objective": plan_raw.get("objective"),
            "requested_tools": requested_tools,
            "target_urls": list(plan_raw.get("target_urls") or []),
            "tool_ids": list(current_tools),
        },
    )
    envelope = ToolResultEnvelope(
        call=spec.call,
        decision=ToolPolicyDecision.INTERRUPT,
        summary=str(raw.get("summary") or "plan approval requested"),
        structured_output={
            **raw,
            "plan_approval": plan_payload.model_dump(mode="json"),
        },
        interrupt=interrupt.model_dump(mode="json"),
        metadata=dict(spec.run_metadata),
    )
    trace = build_tool_trace(
        trace_spec_denied(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            summary="plan approval requested",
            error_code=InterruptReason.PLAN_APPROVAL_REQUIRED.value,
        )
    )
    spec.result.append(envelope=envelope, trace=trace, interrupt=interrupt)
    return True
