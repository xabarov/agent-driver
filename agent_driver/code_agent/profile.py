"""CodeAgent profile loop adapter for single-agent runtime."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from agent_driver.code_agent.executor import CodeExecutionError
from agent_driver.code_agent.parse import parse_code_action
from agent_driver.code_agent.stage_planning import plan_callable_tools
from agent_driver.code_agent.tool_surface import build_callable_tool_surface
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk, ToolTraceStatus
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope, ToolTrace


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


async def run_code_agent_stage(
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

    registry = runner.deps.tool_registry
    tool_specs = build_callable_tool_surface(registry)
    called_tools = _called_tool_names(action.code)
    callable_tools, traces, planned_envelopes, interrupt = plan_callable_tools(
        registry=registry,
        context=context,
        tool_specs=tool_specs,
        called_tools=called_tools,
    )
    if interrupt is not None:
        return CodeAgentStageResult(
            traces=traces,
            envelopes=planned_envelopes,
            interrupt=interrupt,
            has_final_answer=False,
        )
    context.metadata["code_tool_docs"] = "\n".join(
        f"{spec.name}{spec.signature}" for spec in tool_specs
    )

    try:
        result = await runner.deps.code_executor.execute(
            action=action,
            limits=runner.config.code_limits,
            authorized_imports=set(runner.config.authorized_imports),
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
