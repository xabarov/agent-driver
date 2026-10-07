"""Stage 2 runtime integration: bounded JEV escalation and policy invariants."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts.enums import ToolPolicyDecision
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall, ToolError, ToolResultEnvelope
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.quality_gate import QualityGateResult
from agent_driver.llm.rollout import JevRolloutSettings
from agent_driver.runtime.single_agent.tool_stage import (
    _maybe_quality_gate,
    _maybe_recovery_gate,
)
from agent_driver.runtime.tools import ToolExecutionResult


def _context(*, role: str = "fast", metadata: dict | None = None):
    return SimpleNamespace(
        run_id="run_quality",
        attempt_id="attempt_quality",
        metadata=dict(metadata or {}),
        run_input=AgentRunInput(
            input="complete this task",
            agent_id="agent",
            graph_preset="single_react",
            model_role=role,
        ),
        llm_response=LlmResponse(
            message=ChatMessage(role="assistant", content="draft answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(),
            provider="fake",
            model="fast-model",
        ),
        tool_calls=0,
    )


class _Host:
    def __init__(self, gate, *, context=None):
        self._config = SimpleNamespace(quality_gate=gate)
        self.events: list[dict] = []

    def _emit_runtime_decision(self, context, **kwargs):
        self.events.append(kwargs)


class _Gate:
    strong_role = "strong"

    def __init__(self, result: QualityGateResult):
        self.result = result
        self.states: list[dict] = []

    async def evaluate(self, *, state):
        self.states.append(dict(state))
        return self.result


class _RecoveryGate(_Gate):
    async def classify_recovery(self, *, state):
        self.states.append(dict(state))
        return self.result


def _result(*, denied: bool = False) -> ToolExecutionResult:
    if not denied:
        return ToolExecutionResult()
    return ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="write_file", args={}),
                decision=ToolPolicyDecision.DENY,
                error=ToolError(code="policy_denied", message="denied"),
            )
        ]
    )


@pytest.mark.asyncio
async def test_quality_gate_escalates_once_and_preserves_candidate() -> None:
    gate = _Gate(
        QualityGateResult(
            action="escalate_strong",
            reason="jev_low_confidence_or_insufficient",
            metadata={
                "source": "jev",
                "question_schema": "jev-quality.v1",
                "action": "escalate_strong",
                "confidence": 0.4,
            },
            usage=UsageSummary(
                input_tokens=10,
                output_tokens=4,
                cost_usd_estimate=0.001,
                model_name="typesafe/jev-1.13",
            ),
        )
    )
    host = _Host(gate)
    context = _context()

    transitioned = await _maybe_quality_gate(host, context, _result())

    assert transitioned is True
    assert context.metadata["llm_routed_role"] == "strong"
    assert context.metadata["quality_gate_escalations"] == 1
    assert context.metadata["quality_gate_candidate"]["chars"] == len("draft answer")
    assert "draft answer" not in str(context.metadata["quality_gate_candidate"])
    messages = context.metadata["protocol_messages"]
    assert any(item["role"] == "assistant" and item["content"] == "draft answer" for item in messages)
    assert messages[-1]["metadata"]["runtime_scaffolding"] == "jev_quality_gate"
    assert host.events[0]["kind"] == "quality_gate"
    assert host.events[0]["action"] == "select_model_role"
    assert context.metadata["cost_ledger"]["per_model"]["typesafe/jev-1.13"][
        "cost_usd"
    ] == 0.001


@pytest.mark.asyncio
async def test_quality_gate_cannot_override_denied_tool_policy() -> None:
    gate = _Gate(
        QualityGateResult(
            action="continue_tools",
            reason="jev_more_tools_or_evidence",
            metadata={
                "source": "jev",
                "question_schema": "jev-quality.v1",
                "action": "continue_tools",
            },
        )
    )
    host = _Host(gate)
    context = _context()
    result = _result(denied=True)

    transitioned = await _maybe_quality_gate(host, context, result)

    assert transitioned is True
    assert gate.states[0]["policy_denied_tools"] == ["write_file"]
    assert context.run_input.tool_policy.denied_tools is None
    assert context.metadata["quality_gate_decision"]["action"] == "continue_tools"


@pytest.mark.asyncio
async def test_quality_gate_skips_strong_runs_and_force_final() -> None:
    gate = _Gate(
        QualityGateResult(
            action="escalate_strong",
            reason="should_not_run",
            metadata={"source": "jev"},
        )
    )
    host = _Host(gate)

    strong_context = _context(role="strong")
    assert await _maybe_quality_gate(host, strong_context, _result()) is False
    assert gate.states == []

    forced_context = _context(metadata={"force_final_answer": True})
    assert await _maybe_quality_gate(host, forced_context, _result()) is False
    assert gate.states == []


@pytest.mark.asyncio
async def test_quality_gate_escalation_budget_is_bounded() -> None:
    gate = _Gate(
        QualityGateResult(
            action="escalate_strong",
            reason="jev_strong_review",
            metadata={"source": "jev", "action": "escalate_strong"},
        )
    )
    host = _Host(gate)
    context = _context(metadata={"quality_gate_escalations": 1})

    transitioned = await _maybe_quality_gate(host, context, _result())

    assert transitioned is False
    assert context.metadata["quality_gate_decision"]["action"] == "escalate_strong"
    assert "llm_routed_role" not in context.metadata


@pytest.mark.asyncio
async def test_quality_gate_shadow_records_decision_without_transition() -> None:
    gate = _Gate(
        QualityGateResult(
            action="escalate_strong",
            reason="shadow_check",
            metadata={"source": "jev", "action": "escalate_strong"},
        )
    )
    host = _Host(gate)
    host._config.jev_rollout = JevRolloutSettings(mode="shadow")
    context = _context()

    assert await _maybe_quality_gate(host, context, _result()) is False
    assert context.metadata["quality_gate_decision"]["rollout_mode"] == "shadow"
    assert "llm_routed_role" not in context.metadata
    assert host.events[0]["status"] == "shadow"


@pytest.mark.asyncio
async def test_recovery_gate_is_bounded_and_cannot_retry_denied_tool() -> None:
    gate = _RecoveryGate(
        QualityGateResult(
            action="retry_tool",
            reason="jev_recovery_retry_tool",
            metadata={
                "source": "jev",
                "question_schema": "jev-recovery.v1",
                "action": "retry_tool",
            },
        )
    )
    host = _Host(gate)
    context = _context()
    result = _result(denied=True)

    transitioned = await _maybe_recovery_gate(host, context, result)

    assert transitioned is True
    assert gate.states[0]["phase"] == "tool_recovery"
    assert gate.states[0]["policy_denied_tools"] == ["write_file"]
    assert context.metadata["jev_repair_attempts"] == 1
    assert host.events[0]["kind"] == "retry"
    assert host.events[0]["action"] == "retry"

    # A second invocation cannot spend another JEV repair attempt.
    assert await _maybe_recovery_gate(host, context, result) is False
    assert len(gate.states) == 1
