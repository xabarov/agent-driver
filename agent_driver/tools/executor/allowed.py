"""Allow-path tool execution: guardrails, handler, output budgets, final guard."""

from __future__ import annotations

import json
from typing import Any

from agent_driver.contracts.enums import (
    GuardrailDecision,
    InterruptReason,
    ResumeAction,
    ToolPolicyDecision,
)
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.tools import ToolError, ToolResultEnvelope
from agent_driver.tools.executor.blocks import append_blocked_call
from agent_driver.tools.executor.specs import (
    AllowedSpec,
    BlockSpec,
    merge_guardrail_decisions,
)
from agent_driver.tools.executor.trace import (
    build_tool_trace,
    trace_spec_completed,
    trace_spec_denied,
)
from agent_driver.tools.guardrails import GuardrailPipeline, enforce_output_budget
from agent_driver.tools.context import (
    tool_call_context_scope,
    tool_progress_scope,
)
from agent_driver.tools.executor.spill import (
    should_spill_payload,
    spill_payload_to_artifact,
)


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
    attempt_id = str(spec.run_metadata.get("attempt_id") or f"attempt_{spec.index}")
    return run_id, attempt_id


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


async def execute_allowed_path(
    *,
    guardrails: GuardrailPipeline,
    spec: AllowedSpec,
) -> bool:
    """Execute allow-path flow including guardrails and budgets."""
    args_guard = await guardrails.on_tool_args(
        {"tool_name": spec.call.tool_name, "args": spec.call.args}
    )
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
        return False
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
        return False
    try:
        # Phase 11 H16 — wire a per-call progress reporter that records
        # each ``report_tool_progress`` invocation into the executor
        # result. Tools that don't call the reporter incur no overhead.
        def _record_progress(progress) -> None:  # noqa: ANN001
            spec.result.record_progress(
                call_index=spec.index,
                tool_name=spec.call.tool_name,
                progress=progress,
            )

        with tool_call_context_scope(
            run_id=str(spec.run_metadata.get("run_id") or ""),
            thread_id=str(spec.run_metadata.get("thread_id") or ""),
        ), tool_progress_scope(_record_progress):
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
                tool_call_id=str(spec.run_metadata.get("attempt_id") or ""),
            )
            if spilled is not None:
                raw = spilled[0]
        raw, structured_truncated = _bounded_structured_output(
            raw,
            max_chars=spec.manifest.output_char_budget,
        )
        summary = raw.get("summary") if isinstance(raw.get("summary"), str) else None
        bounded_summary, truncated = enforce_output_budget(
            summary, spec.manifest.output_char_budget
        )
        envelope = ToolResultEnvelope(
            call=spec.call,
            decision=ToolPolicyDecision.ALLOW,
            guardrail_decision=merge_guardrail_decisions(
                spec.input_guard_decision,
                args_guard.decision,
                raw_guard.decision,
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
        else:
            envelope = envelope.model_copy(
                update={
                    "guardrail_decision": merge_guardrail_decisions(
                        envelope.guardrail_decision,
                        final_guard.decision,
                    )
                }
            )
            trace = build_tool_trace(
                trace_spec_completed(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    summary=envelope.summary,
                    truncated=envelope.truncated,
                )
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
    allow_multiple = bool(raw.get("allow_multiple", False))
    run_id, attempt_id = _interrupt_identifiers(spec)
    interrupt = InterruptRequest(
        interrupt_id=f"int_{spec.call.tool_call_id or spec.index}",
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
