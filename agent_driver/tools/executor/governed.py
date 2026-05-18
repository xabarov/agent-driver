"""Governed tool executor: policy, interrupts, and staged guardrails."""

from __future__ import annotations

from agent_driver.contracts.enums import GuardrailDecision, ToolPolicyDecision
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmResponse
from agent_driver.tools.executor.allowed import execute_allowed_path
from agent_driver.tools.executor.blocks import append_blocked_call
from agent_driver.tools.executor.planned import extract_planned_tool_calls
from agent_driver.tools.executor.policy_interrupt import record_interrupt_and_trace
from agent_driver.tools.executor.result import GovernedExecutionResult
from agent_driver.tools.executor.specs import (
    AllowedSpec,
    BlockSpec,
    ExecSpec,
    ToolApprovalContext,
    safe_manifest,
)
from agent_driver.tools.guardrails import GuardrailPipeline
from agent_driver.tools.policy import evaluate_tool_policy
from agent_driver.tools.registry import ToolRegistry


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
        return extract_planned_tool_calls(llm_response)

    def _append_block(
        self,
        *,
        result: GovernedExecutionResult,
        spec: BlockSpec,
    ) -> None:
        append_blocked_call(result=result, spec=spec)

    async def execute(
        self,
        run_input: AgentRunInput,
        llm_response: LlmResponse,
        *,
        current_tool_calls: int = 0,
    ) -> GovernedExecutionResult:
        """Run policy + guardrails + tool handlers for planned calls."""
        result = GovernedExecutionResult()
        planned_calls = extract_planned_tool_calls(llm_response)
        for index, call in enumerate(planned_calls, start=1):
            stop = await self._execute_one_call(
                ExecSpec(
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

    async def _execute_one_call(self, spec: ExecSpec) -> bool:
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
            else safe_manifest(call.tool_name)
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
            self._append_block(
                result=result,
                spec=BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    code="policy_denied",
                    reason=policy.reason,
                ),
            )
            return False
        if policy.decision == ToolPolicyDecision.INTERRUPT:
            record_interrupt_and_trace(
                result,
                ToolApprovalContext(
                    run_input=run_input,
                    call=call,
                    index=index,
                    manifest=manifest,
                    policy=policy,
                    run_metadata=run_metadata,
                ),
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
            self._append_block(
                result=result,
                spec=BlockSpec(
                    index=index,
                    call=call,
                    manifest=manifest,
                    reason=input_guard.reason or "guardrail blocked tool input",
                    code="guardrail_blocked",
                    stage="input",
                ),
            )
            return False
        return await execute_allowed_path(
            guardrails=self._guardrails,
            spec=AllowedSpec(
                result=result,
                call=call,
                index=index,
                manifest=manifest,
                registered=registered,
                input_guard_decision=input_guard.decision,
                run_metadata=run_metadata,
            ),
        )
