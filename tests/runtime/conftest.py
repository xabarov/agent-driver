"""Shared helpers for runtime integration tests (governance / HITL harness)."""

from __future__ import annotations

from agent_driver.contracts import (
    ApprovalMode,
    GuardrailDecision,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.runtime import GuardrailPipeline, GuardrailResult


def danger_tool_manifest() -> ToolManifest:
    """High-risk manifest used across HITL policy tests."""
    return ToolManifest(
        name="danger",
        description="Danger",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ALWAYS,
    )


def planned_danger_tool_policy() -> ToolPolicyInput:
    """Tool policy that plans a dangerous call requiring approval."""
    return ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        approval_required_for_risk=ToolRisk.HIGH,
        metadata={
            "planned_tool_calls": [{"tool_name": "danger", "args": {"target": "x"}}]
        },
    )


def llm_request_with_planned_calls(planned: list[ToolCall]) -> LlmRequest:
    """Build LLM request carrying JSON-safe planned tool calls."""
    return LlmRequest(
        messages=[ChatMessage(role="user", content="hello")],
        metadata={
            "planned_tool_calls": [call.model_dump(mode="json") for call in planned]
        },
    )


class BlockingToolArgsGuardrails(GuardrailPipeline):
    """Blocks execution when args contain ``blocked: True``."""

    async def on_tool_args(self, payload: dict[str, object]) -> GuardrailResult:
        if payload.get("args", {}).get("blocked"):
            return GuardrailResult(
                decision=GuardrailDecision.BLOCK,
                reason="args blocked by guardrail",
            )
        return await super().on_tool_args(payload)


class BlockingToolInputGuardrails(GuardrailPipeline):
    """Blocks lookup tools at the input validation stage."""

    async def on_input(self, payload: dict[str, object]) -> GuardrailResult:
        if payload.get("tool_name") == "lookup":
            return GuardrailResult(
                decision=GuardrailDecision.BLOCK,
                reason="input blocked by guardrail",
            )
        return await super().on_input(payload)


class SanitizeToolResultGuardrails(GuardrailPipeline):
    """Marks tool results for sanitization."""

    async def on_tool_result(self, payload: dict[str, object]) -> GuardrailResult:
        _ = payload
        return GuardrailResult(
            decision=GuardrailDecision.SANITIZE,
            reason="sanitize marker",
        )
