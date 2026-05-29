"""Tool planning helpers for code-agent stage execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_driver.code_agent.tool_surface import CallableToolSpec
from agent_driver.contracts.enums import (
    AgentProfile,
    SideEffectClass,
    ToolPolicyDecision,
)
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope, ToolTrace
from agent_driver.tools.executor.policy_interrupt import build_tool_approval_interrupt
from agent_driver.tools.executor.specs import ToolApprovalContext
from agent_driver.tools.executor.trace import build_tool_trace, trace_spec_denied
from agent_driver.tools.policy import evaluate_tool_policy
from agent_driver.tools.registry import RegisteredTool, ToolRegistry


def plan_callable_tools(
    *,
    registry: ToolRegistry,
    context: Any,
    tool_specs: list[CallableToolSpec],
    called_tools: set[str],
) -> tuple[dict[str, Callable[..., object]], list[ToolTrace], list[ToolResultEnvelope], Any | None]:
    """Resolve callable tool handlers; return early interrupt payload when required."""
    callable_tools: dict[str, Callable[..., object]] = {}
    traces: list[ToolTrace] = []
    envelopes: list[ToolResultEnvelope] = []
    for spec in tool_specs:
        registered: RegisteredTool | None = registry.get(spec.name)
        if registered is None:
            continue
        tool_call = ToolCall(tool_name=spec.name, args={})
        policy = (
            evaluate_tool_policy(
                policy=context.run_input.tool_policy,
                manifest=registered.manifest,
                call=tool_call,
                current_tool_calls=context.tool_calls,
            )
            if spec.name in called_tools
            else None
        )
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
            envelopes.append(
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
            return callable_tools, traces, envelopes, interrupt
        callable_tools[spec.name] = registered.handler
    return callable_tools, traces, envelopes, None


__all__ = ["plan_callable_tools"]
