"""CodeAgent profile loop adapter for single-agent runtime."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_driver.code_agent.executor import CodeExecutionError
from agent_driver.code_agent.parse import parse_code_action
from agent_driver.code_agent.tool_surface import build_callable_tool_surface
from agent_driver.contracts.enums import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolPolicyDecision,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope, ToolTrace
from agent_driver.tools.executor.policy_interrupt import build_tool_approval_interrupt
from agent_driver.tools.executor.specs import ToolApprovalContext
from agent_driver.tools.executor.trace import build_tool_trace, trace_spec_denied
from agent_driver.tools.policy import evaluate_tool_policy
from agent_driver.tools.registry import RegisteredTool


@dataclass(slots=True)
class CodeAgentStageResult:
    """Duck-typed tool-stage result for code-agent execution."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: Any = None
    has_final_answer: bool = False


def _called_tool_names(code: str) -> set[str]:
    """Extract direct callable names referenced in code action."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


async def run_code_agent_stage(  # pylint: disable=too-many-locals
    *,
    runner: Any,
    context: Any,
) -> CodeAgentStageResult:
    """Execute code-agent stage with approval and policy checks."""
    if context.llm_response is None:
        return CodeAgentStageResult(has_final_answer=False)
    action = parse_code_action(context.llm_response)
    if action is None:
        # No executable action means model already produced direct answer text.
        return CodeAgentStageResult(has_final_answer=True)

    registry = runner._deps.tool_registry  # pylint: disable=protected-access
    tool_specs = build_callable_tool_surface(registry)
    called_tools = _called_tool_names(action.code)
    callable_tools: dict[str, Callable[..., object]] = {}
    planned_envelopes: list[ToolResultEnvelope] = []
    traces: list[ToolTrace] = []
    for spec in tool_specs:
        registered: RegisteredTool | None = registry.get(spec.name)
        if registered is None:
            continue
        tool_call = ToolCall(tool_name=spec.name, args={})
        if spec.name in called_tools:
            policy = evaluate_tool_policy(
                policy=context.run_input.tool_policy,
                manifest=registered.manifest,
                call=tool_call,
                current_tool_calls=context.tool_calls,
            )
        else:
            policy = None
        if (
            spec.name in called_tools
            and spec.side_effect
            in {
                SideEffectClass.EXTERNAL_ACTION,
                SideEffectClass.IRREVERSIBLE_WRITE,
            }
            and policy is not None
        ):
            interrupt = build_tool_approval_interrupt(
                ToolApprovalContext(
                    run_input=context.run_input,
                    call=tool_call,
                    index=context.tool_calls + 1,
                    manifest=registered.manifest,
                    policy=policy,
                    run_metadata={"agent_profile": AgentProfile.CODE_AGENT.value},
                )
            )
            planned_envelopes.append(
                ToolResultEnvelope(
                    call=tool_call,
                    decision=ToolPolicyDecision.INTERRUPT,
                    interrupt=interrupt.model_dump(mode="json"),
                )
            )
            traces.append(
                build_tool_trace(
                    trace_spec_denied(
                        index=context.tool_calls + 1,
                        call=tool_call,
                        manifest=registered.manifest,
                        summary="approval required",
                        error_code="approval_required",
                    )
                )
            )
            return CodeAgentStageResult(
                traces=traces,
                envelopes=planned_envelopes,
                interrupt=interrupt,
                has_final_answer=False,
            )
        callable_tools[spec.name] = registered.handler
    context.metadata["code_tool_docs"] = "\n".join(
        f"{spec.name}{spec.signature}" for spec in tool_specs
    )

    try:
        result = await runner._deps.code_executor.execute(  # pylint: disable=protected-access
            action=action,
            limits=runner._config.code_limits,  # pylint: disable=protected-access
            authorized_imports=set(
                runner._config.authorized_imports  # pylint: disable=protected-access
            ),
            serialization_policy=context.run_input.serialization_policy,
            callable_tools=callable_tools,
        )
    except CodeExecutionError as exc:
        envelope = ToolResultEnvelope(
            call=ToolCall(
                tool_name="code_action", args={"action_id": action.action_id}
            ),
            summary=f"interpreter_error: {exc}",
            metadata={"phase": "code_agent"},
        )
        trace = ToolTrace(
            step=context.tool_calls + 1,
            tool_name="code_action",
            status=ToolTraceStatus.FAILED,
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            result_summary=envelope.summary,
            error_code="interpreter_error",
        )
        return CodeAgentStageResult(
            envelopes=[envelope], traces=[trace], has_final_answer=False
        )
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name="code_action", args={"action_id": action.action_id}),
        summary=(
            result.final_answer.text if result.final_answer else "code action completed"
        ),
        structured_output=result.model_dump(mode="json"),
        metadata={
            "phase": "code_agent",
            "tool_docs": context.metadata["code_tool_docs"],
        },
    )
    trace = ToolTrace(
        step=context.tool_calls + 1,
        tool_name="code_action",
        status=ToolTraceStatus.COMPLETED,
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        result_summary=envelope.summary,
    )
    return CodeAgentStageResult(
        envelopes=[envelope],
        traces=[trace],
        has_final_answer=result.final_answer is not None,
    )


__all__ = ["run_code_agent_stage"]
