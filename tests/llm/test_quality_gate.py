"""Stage 2 JEV quality-gate contracts and bounded outcomes."""

from __future__ import annotations

import pytest

from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.decision_contracts import (
    DecisionAnswer,
    DecisionResponse,
)
from agent_driver.llm.quality_gate import JevQualityGate


def _response(
    *,
    action: str,
    sufficient: float = 0.95,
    needs_tools: float = 0.05,
    grounded: float = 0.95,
    confidence: float = 0.95,
) -> DecisionResponse:
    labels = {
        "finalize": "The answer is ready to return to the user.",
        "continue_tools": "Allowed tools or evidence are still needed.",
        "escalate_strong": "A stronger model should review or repair the answer.",
        "ask_user": "A material ambiguity requires one user clarification.",
    }
    probabilities = {key: 0.02 for key in labels}
    probabilities[action] = 0.94
    return DecisionResponse(
        model="typesafe/jev-1.13",
        model_version="typesafe/jev-1.13-20260917",
        request_id="req_quality_1",
        provider="TypeSafe",
        answers={
            "next_action": DecisionAnswer(
                question_id="next_action",
                type="choice",
                choice=action,
                probabilities=probabilities,
                confidence=confidence,
            ),
            "sufficient": DecisionAnswer(
                question_id="sufficient", type="noul", noul=sufficient
            ),
            "needs_tools": DecisionAnswer(
                question_id="needs_tools", type="noul", noul=needs_tools
            ),
            "grounded": DecisionAnswer(
                question_id="grounded", type="noul", noul=grounded
            ),
        },
        input_tokens=21,
        output_tokens=7,
        cost_usd=0.0002,
        latency_ms=12.0,
    )


class _Provider:
    def __init__(self, response: DecisionResponse | Exception) -> None:
        self.response = response
        self.states: list[dict] = []

    async def decide(self, *, state, questions, model=None):
        self.states.append(dict(state))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_quality_gate_accepts_grounded_high_confidence_answer() -> None:
    provider = _Provider(_response(action="finalize"))
    gate = JevQualityGate(decision_provider=provider)

    result = await gate.evaluate(
        state={"request": "question", "candidate_answer": "answer"}
    )

    assert result.action == "accept"
    assert result.reason == "jev_answer_sufficient"
    assert result.usage is not None
    assert provider.states == [{"request": "question", "candidate_answer": "answer"}]
    assert "candidate_answer" not in result.metadata


@pytest.mark.asyncio
async def test_quality_gate_escalates_when_answer_is_not_sufficient() -> None:
    provider = _Provider(
        _response(action="finalize", sufficient=0.2, grounded=0.2, confidence=0.9)
    )
    gate = JevQualityGate(decision_provider=provider)

    result = await gate.evaluate(
        state={"request": "question", "candidate_answer": "draft"}
    )

    assert result.action == "escalate_strong"
    assert result.reason == "jev_low_confidence_or_insufficient"


@pytest.mark.asyncio
async def test_quality_gate_preserves_ambiguity_as_ask_user() -> None:
    provider = _Provider(_response(action="ask_user"))
    gate = JevQualityGate(decision_provider=provider)

    result = await gate.evaluate(state={"request": "ambiguous"})

    assert result.action == "ask_user"
    assert result.reason == "jev_material_ambiguity"


@pytest.mark.asyncio
async def test_quality_gate_failure_accepts_without_raising() -> None:
    provider = _Provider(TimeoutError())
    gate = JevQualityGate(decision_provider=provider)

    result = await gate.evaluate(state={"request": "question"})

    assert result.action == "accept"
    assert result.metadata["source"] == "fallback"
    assert result.metadata["fallback_reason"] == "TimeoutError"
    assert gate.last_decision_usage is None


@pytest.mark.asyncio
async def test_quality_gate_usage_maps_to_cost_ledger_contract() -> None:
    provider = _Provider(_response(action="continue_tools"))
    gate = JevQualityGate(decision_provider=provider)

    result = await gate.evaluate(state={"request": "need evidence"})

    assert result.action == "continue_tools"
    assert result.usage == UsageSummary(
        input_tokens=21,
        output_tokens=7,
        cost_usd_estimate=0.0002,
        model_name="typesafe/jev-1.13",
        model_provider="TypeSafe",
        metadata={"aux_task": "quality_gate", "usage_known": True},
    )


@pytest.mark.asyncio
async def test_quality_gate_classifies_one_retryable_recovery() -> None:
    response = DecisionResponse(
        model="typesafe/jev-1.13",
        answers={
            "repair_action": DecisionAnswer(
                question_id="repair_action",
                type="choice",
                choice="repair_prompt",
                probabilities={
                    "retry_tool": 0.05,
                    "repair_prompt": 0.9,
                    "ask_user": 0.03,
                    "accept": 0.02,
                },
                confidence=0.9,
            ),
            "retryable": DecisionAnswer(
                question_id="retryable", type="noul", noul=0.9
            ),
        },
    )
    provider = _Provider(response)
    gate = JevQualityGate(decision_provider=provider)

    result = await gate.classify_recovery(
        state={"phase": "tool_recovery", "parse_error_codes": ["bad_json"]}
    )

    assert result.action == "repair_prompt"
    assert result.metadata["question_schema"] == "jev-recovery.v1"
    assert result.usage is not None
