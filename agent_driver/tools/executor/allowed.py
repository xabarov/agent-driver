"""Allow-path tool execution: guardrails, handler, output budgets, final guard."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import GuardrailDecision, ToolPolicyDecision
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
        append_blocked_call(
            result=spec.result,
            spec=BlockSpec(
                index=spec.index,
                call=spec.call,
                manifest=spec.manifest,
                code="tool_not_registered",
                reason="tool is not registered",
            ),
        )
        return False
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
        truncated=truncated,
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
    return False
