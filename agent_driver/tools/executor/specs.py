"""Internal dataclass specs for governed tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_driver.contracts.enums import (
    ApprovalMode,
    GuardrailDecision,
    SideEffectClass,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall, ToolManifest, ToolPolicyOutcome
from agent_driver.tools.executor.result import GovernedExecutionResult
from agent_driver.tools.registry import RegisteredTool


@dataclass(frozen=True, slots=True)
class TraceSpec:
    """Compact trace build specification."""

    index: int
    call: ToolCall
    manifest: ToolManifest
    status: ToolTraceStatus
    summary: str | None = None
    error_code: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class BlockSpec:
    """Shared block payload for policy/guardrail denials."""

    index: int
    call: ToolCall
    manifest: ToolManifest
    reason: str
    code: str
    stage: str | None = None


@dataclass(frozen=True, slots=True)
class ExecSpec:
    """Execution inputs grouped to keep method signatures compact."""

    result: GovernedExecutionResult
    run_input: AgentRunInput
    call: ToolCall
    index: int
    current_tool_calls: int


@dataclass(frozen=True, slots=True)
class AllowedSpec:
    """Allow-path inputs for one registered/unregistered tool call."""

    result: GovernedExecutionResult
    call: ToolCall
    index: int
    manifest: ToolManifest
    registered: RegisteredTool | None
    input_guard_decision: GuardrailDecision = GuardrailDecision.ALLOW
    run_metadata: dict[str, str | int | None] = field(default_factory=dict)
    # Phase 12 H18 — optional artifact store for spilling oversized
    # tool handler outputs. When ``None`` (default), no spill happens;
    # legacy ``output_char_budget`` truncation runs as before.
    artifact_store: Any = None


@dataclass(frozen=True, slots=True)
class ToolApprovalContext:
    """Inputs for human-approval (HITL) interrupt assembly for one tool call."""

    run_input: AgentRunInput
    call: ToolCall
    index: int
    manifest: ToolManifest
    policy: ToolPolicyOutcome
    run_metadata: dict[str, str | int | None]


def safe_manifest(name: str) -> ToolManifest:
    """Fallback manifest for unregistered tools."""
    return ToolManifest(
        name=name,
        description="unregistered tool fallback",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ALWAYS,
    )


def merge_guardrail_decisions(*decisions: GuardrailDecision) -> GuardrailDecision:
    """Collapse multiple hook decisions into one envelope-level decision."""
    if any(decision == GuardrailDecision.BLOCK for decision in decisions):
        return GuardrailDecision.BLOCK
    if any(decision == GuardrailDecision.SANITIZE for decision in decisions):
        return GuardrailDecision.SANITIZE
    return GuardrailDecision.ALLOW
