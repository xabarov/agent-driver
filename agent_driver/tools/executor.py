"""Governed tool executor based on registry, policy, and guardrails."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agent_driver.contracts.enums import (
    ApprovalMode,
    GuardrailDecision,
    InterruptReason,
    ResumeAction,
    SideEffectClass,
    ToolPolicyDecision,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import (
    ToolCall,
    ToolError,
    ToolManifest,
    ToolResultEnvelope,
    ToolTrace,
)
from agent_driver.llm.contracts import LlmResponse
from agent_driver.tools.guardrails import GuardrailPipeline, enforce_output_budget
from agent_driver.tools.policy import evaluate_tool_policy
from agent_driver.tools.registry import RegisteredTool, ToolRegistry


@dataclass(slots=True)
class GovernedExecutionResult:
    """Detailed executor result used for runtime integration."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: InterruptRequest | None = None

    def append(
        self,
        *,
        envelope: ToolResultEnvelope,
        trace: ToolTrace,
        interrupt: InterruptRequest | None = None,
    ) -> None:
        """Append envelope/trace pair and optional interrupt."""
        self.envelopes.append(envelope)
        self.traces.append(trace)
        if interrupt is not None:
            self.interrupt = interrupt


@dataclass(frozen=True, slots=True)
class _TraceSpec:
    """Compact trace build specification."""

    index: int
    call: ToolCall
    manifest: ToolManifest
    status: ToolTraceStatus
    summary: str | None = None
    error_code: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class _BlockSpec:
    """Shared block payload for policy/guardrail denials."""

    index: int
    call: ToolCall
    manifest: ToolManifest
    reason: str
    code: str
    stage: str | None = None


@dataclass(frozen=True, slots=True)
class _ExecSpec:
    """Execution inputs grouped to keep method signatures compact."""

    result: GovernedExecutionResult
    run_input: AgentRunInput
    call: ToolCall
    index: int
    current_tool_calls: int


@dataclass(frozen=True, slots=True)
class _AllowedSpec:
    """Allow-path inputs for one registered/unregistered tool call."""

    result: GovernedExecutionResult
    call: ToolCall
    index: int
    manifest: ToolManifest
    registered: RegisteredTool | None
    input_guard_decision: GuardrailDecision = GuardrailDecision.ALLOW
    run_metadata: dict[str, str | int | None] = field(default_factory=dict)


def _safe_manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description="unregistered tool fallback",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ALWAYS,
    )


def _merge_guardrail_decisions(*decisions: GuardrailDecision) -> GuardrailDecision:
    """Collapse multiple hook decisions into one envelope-level decision."""
    if any(decision == GuardrailDecision.BLOCK for decision in decisions):
        return GuardrailDecision.BLOCK
    if any(decision == GuardrailDecision.SANITIZE for decision in decisions):
        return GuardrailDecision.SANITIZE
    return GuardrailDecision.ALLOW


class GovernedToolExecutor:
    """Execute deterministic planned tool calls with policy and guardrails."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        guardrails: GuardrailPipeline | None = None,
    ) -> None:
        self._registry = registry
        self._guardrails = guardrails or GuardrailPipeline()

    @staticmethod
    def planned_calls(llm_response: LlmResponse) -> list[ToolCall]:
        """Parse planned tool calls from LLM response metadata."""
        payload = llm_response.metadata.get("planned_tool_calls")
        if not isinstance(payload, list):
            return []
        calls: list[ToolCall] = []
        for item in payload:
            if isinstance(item, dict):
                calls.append(ToolCall.model_validate(item))
        return calls

    @staticmethod
    def _trace(spec: _TraceSpec) -> ToolTrace:
        """Build ToolTrace from compact trace specification."""
        return ToolTrace(
            step=spec.index,
            tool_name=spec.call.tool_name,
            tool_call_id=spec.call.tool_call_id,
            status=spec.status,
            risk=spec.manifest.risk,
            side_effect=spec.manifest.side_effect,
            approval_mode=spec.manifest.approval_mode,
            result_summary=spec.summary,
            truncated=spec.truncated,
            error_code=spec.error_code,
        )

    async def _append_block(
        self,
        *,
        result: GovernedExecutionResult,
        spec: _BlockSpec,
    ) -> None:
        metadata = {"policy_reason": spec.reason}
        if spec.stage is not None:
            metadata = {"guardrail_stage": spec.stage}
        result.append(
            envelope=ToolResultEnvelope(
                call=spec.call,
                decision=ToolPolicyDecision.DENY,
                guardrail_decision=(
                    GuardrailDecision.BLOCK
                    if spec.stage is not None
                    else GuardrailDecision.ALLOW
                ),
                error=ToolError(code=spec.code, message=spec.reason),
                metadata=metadata,
            ),
            trace=self._trace(
                _TraceSpec(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    status=ToolTraceStatus.DENIED,
                    summary=spec.reason,
                    error_code=spec.code,
                )
            ),
        )

    async def execute(
        self,
        run_input: AgentRunInput,
        llm_response: LlmResponse,
        *,
        current_tool_calls: int = 0,
    ) -> GovernedExecutionResult:
        """Run policy + guardrails + tool handlers for planned calls."""
        result = GovernedExecutionResult()
        planned_calls = self.planned_calls(llm_response)
        for index, call in enumerate(planned_calls, start=1):
            stop = await self._execute_one_call(
                _ExecSpec(
                    result=result,
                    run_input=run_input,
                    call=call,
                    index=index,
                    current_tool_calls=current_tool_calls,
                )
            )
            if stop:
                break
        return result

    async def _execute_one_call(self, spec: _ExecSpec) -> bool:
        """Execute one tool call, returning True when loop must stop."""
        result = spec.result
        run_input = spec.run_input
        call = spec.call
        index = spec.index
        run_metadata = {
            "agent_profile": run_input.agent_profile.value,
            "prompt_template_id": run_input.prompt_template_id,
            "prompt_template_version": run_input.prompt_template_version,
        }
        registered = self._registry.get(call.tool_name)
        manifest = (
            registered.manifest
            if registered is not None
            else _safe_manifest(call.tool_name)
        )
        policy = evaluate_tool_policy(
            policy=run_input.tool_policy,
            manifest=manifest,
            call=call,
            current_tool_calls=spec.current_tool_calls + len(result.traces),
        )
        approved_interrupt_id = call.metadata.get("approved_interrupt_id")
        if (
            policy.decision == ToolPolicyDecision.INTERRUPT
            and isinstance(approved_interrupt_id, str)
            and approved_interrupt_id.strip()
        ):
            policy = policy.model_copy(
                update={
                    "decision": ToolPolicyDecision.ALLOW,
                    "reason": "approval previously granted",
                    "interrupt_reason": None,
                }
            )
        if policy.decision == ToolPolicyDecision.DENY:
            await self._append_block(
                result=result,
                spec=_BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    code="policy_denied",
                    reason=policy.reason,
                ),
            )
            return False
        if policy.decision == ToolPolicyDecision.INTERRUPT:
            args_preview = json.dumps(call.args, ensure_ascii=True, sort_keys=True)
            if len(args_preview) > 280:
                args_preview = args_preview[:280].rstrip() + "..."
            interrupt = InterruptRequest(
                interrupt_id=f"int_{run_input.run_id or 'runtime'}_{index}",
                run_id=run_input.run_id or "run_pending",
                attempt_id=f"attempt_{index}",
                checkpoint_id="checkpoint_pending",
                reason=InterruptReason(policy.interrupt_reason or "approval_required"),
                title=f"Approval required for '{call.tool_name}'",
                description=policy.reason,
                risk=manifest.risk,
                proposed_action={
                    "tool_name": call.tool_name,
                    "tool_call_id": call.tool_call_id,
                    "args": call.args,
                    "args_preview": args_preview,
                    "risk": manifest.risk.value,
                    "side_effect": manifest.side_effect.value,
                    "approval_mode": manifest.approval_mode.value,
                },
                allowed_actions=[
                    ResumeAction.APPROVE,
                    ResumeAction.REJECT,
                    ResumeAction.EDIT,
                    ResumeAction.CANCEL,
                ],
                editable_fields=["args"],
                metadata={
                    "policy_reason": policy.reason,
                    **run_metadata,
                },
            )
            result.append(
                envelope=ToolResultEnvelope(
                    call=call,
                    decision=policy.decision,
                    interrupt=interrupt.model_dump(mode="json"),
                    metadata={
                        "policy_reason": policy.reason,
                        **run_metadata,
                    },
                ),
                trace=self._trace(
                    _TraceSpec(
                        index=index,
                        call=call,
                        manifest=manifest,
                        status=ToolTraceStatus.DENIED,
                        summary=policy.reason,
                        error_code="approval_required",
                    )
                ),
                interrupt=interrupt,
            )
            return True
        input_guard = await self._guardrails.on_input(
            {
                "run_id": run_input.run_id,
                "tool_name": call.tool_name,
                "args": call.args,
            }
        )
        if input_guard.decision == GuardrailDecision.BLOCK:
            await self._append_block(
                result=result,
                spec=_BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    reason=input_guard.reason or "guardrail blocked tool input",
                    code="guardrail_blocked",
                    stage="input",
                ),
            )
            return False
        return await self._execute_allowed_call(
            _AllowedSpec(
                result=result,
                call=call,
                index=index,
                manifest=manifest,
                registered=registered,
                input_guard_decision=input_guard.decision,
                run_metadata=run_metadata,
            )
        )

    async def _execute_allowed_call(self, spec: _AllowedSpec) -> bool:
        """Execute allow-path flow including guardrails and budgets."""
        args_guard = await self._guardrails.on_tool_args(
            {"tool_name": spec.call.tool_name, "args": spec.call.args}
        )
        if args_guard.decision == GuardrailDecision.BLOCK:
            await self._append_block(
                result=spec.result,
                spec=_BlockSpec(
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
            await self._append_block(
                result=spec.result,
                spec=_BlockSpec(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    code="tool_not_registered",
                    reason="tool is not registered",
                ),
            )
            return False
        raw = await spec.registered.handler(spec.call.args)
        raw_guard = await self._guardrails.on_tool_result(
            {"tool_name": spec.call.tool_name, "result": raw}
        )
        if raw_guard.decision == GuardrailDecision.BLOCK:
            await self._append_block(
                result=spec.result,
                spec=_BlockSpec(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    reason=raw_guard.reason or "guardrail blocked tool result",
                    code="guardrail_blocked",
                    stage="tool_result",
                ),
            )
            return False
        summary = raw.get("summary") if isinstance(raw.get("summary"), str) else None
        bounded_summary, truncated = enforce_output_budget(
            summary, spec.manifest.output_char_budget
        )
        envelope = ToolResultEnvelope(
            call=spec.call,
            decision=ToolPolicyDecision.ALLOW,
            guardrail_decision=_merge_guardrail_decisions(
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
        final_guard = await self._guardrails.on_final_output(
            envelope.model_dump(mode="json")
        )
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
            trace = self._trace(
                _TraceSpec(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    status=ToolTraceStatus.DENIED,
                    error_code="guardrail_blocked",
                )
            )
        else:
            envelope = envelope.model_copy(
                update={
                    "guardrail_decision": _merge_guardrail_decisions(
                        envelope.guardrail_decision,
                        final_guard.decision,
                    )
                }
            )
            trace = self._trace(
                _TraceSpec(
                    index=spec.index,
                    call=spec.call,
                    manifest=spec.manifest,
                    status=ToolTraceStatus.COMPLETED,
                    summary=envelope.summary,
                    truncated=envelope.truncated,
                )
            )
        spec.result.append(
            envelope=envelope,
            trace=trace,
        )
        return False
