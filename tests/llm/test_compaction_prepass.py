"""Stage 3 JEV compaction retention decisions."""

from __future__ import annotations

import pytest

from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.compaction_prepass import (
    JevCompactionPrepass,
    CompactionUnit,
)
from agent_driver.llm.decision_contracts import DecisionAnswer, DecisionResponse


def _answer(
    question_id: str,
    question_type: str,
    *,
    score: float = 0.0,
    noul: float = 0.1,
    confidence: float = 0.95,
) -> DecisionAnswer:
    if question_type == "score":
        probabilities = {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0}
        bucket = min(3, max(0, round(score)))
        probabilities = {str(i): 0.0 for i in range(4)}
        probabilities[str(bucket)] = 0.98
        remainder = (1.0 - probabilities[str(bucket)]) / 3
        for key in probabilities:
            if key != str(bucket):
                probabilities[key] = remainder
        expected = sum(int(key) * value for key, value in probabilities.items())
        return DecisionAnswer(
            question_id=question_id,
            type="score",
            score=expected,
            probabilities=probabilities,
            confidence=confidence,
        )
    return DecisionAnswer(question_id=question_id, type="noul", noul=noul)


class _Provider:
    def __init__(self, *, low_confidence: bool = False) -> None:
        self.states: list[dict] = []
        self.low_confidence = low_confidence

    async def decide(self, *, state, questions, model=None):
        self.states.append(state)
        answers = {}
        for key, question in questions.items():
            unit_number = int(key.split("_")[1])
            confidence = 0.4 if self.low_confidence and question.type == "score" else 0.95
            if unit_number == 0 and key.endswith("relevance"):
                answers[key] = _answer(key, question.type, score=0, confidence=confidence)
            elif unit_number == 0 and key.endswith("safe_to_remove"):
                answers[key] = _answer(key, question.type, noul=0.95)
            elif unit_number == 1 and key.endswith("relevance"):
                answers[key] = _answer(key, question.type, score=3, confidence=confidence)
            elif unit_number == 1 and key.endswith("safe_to_remove"):
                answers[key] = _answer(key, question.type, noul=0.1)
            else:
                answers[key] = _answer(key, question.type, noul=0.1)
        return DecisionResponse(
            model="typesafe/jev-1.13",
            model_version="typesafe/jev-1.13-20260917",
            request_id="req_compaction_1",
            provider="TypeSafe",
            answers=answers,
            input_tokens=80,
            output_tokens=30,
            cost_usd=0.0003,
            latency_ms=14.0,
        )


class _MixedConfidenceProvider:
    async def decide(self, *, state, questions, model=None):
        answers = {}
        for key, question in questions.items():
            unit_number = int(key.split("_")[1])
            if question.type == "score":
                score = 0.0 if unit_number == 0 else 3.0
                answers[key] = _answer(
                    key,
                    question.type,
                    score=score,
                    confidence=0.95 if unit_number == 0 else 0.4,
                )
            else:
                answers[key] = _answer(
                    key,
                    question.type,
                    noul=0.95 if unit_number == 0 and key.endswith("safe_to_remove") else 0.1,
                )
        return DecisionResponse(
            model="typesafe/jev-1.13",
            answers=answers,
            input_tokens=20,
            output_tokens=10,
            cost_usd=0.0001,
        )


@pytest.mark.asyncio
async def test_prepass_archives_only_high_confidence_low_relevance_units() -> None:
    provider = _Provider()
    prepass = JevCompactionPrepass(decision_provider=provider)
    result = await prepass.classify(
        active_state={"current_request": "continue the active task"},
        units=[
            CompactionUnit("old", (0,), "user", "old unrelated detail"),
            CompactionUnit("live", (1,), "user", "current constraint", protected=True),
        ],
    )

    assert result.archive_indexes == frozenset({0})
    assert [item.retention for item in result.decisions] == ["archive", "protected"]
    assert result.usage == UsageSummary(
        input_tokens=80,
        output_tokens=30,
        cost_usd_estimate=0.0003,
        model_name="typesafe/jev-1.13",
        model_provider="TypeSafe",
        metadata={"aux_task": "compaction_prepass", "usage_known": True},
    )
    assert provider.states[0]["units"][0]["text"] == "old unrelated detail"
    assert "current_request" not in result.receipt
    assert "old unrelated detail" not in str(result.receipt)
    assert result.receipt["archived_unit_sha256"]


@pytest.mark.asyncio
async def test_prepass_low_confidence_fails_open_and_keeps_context() -> None:
    prepass = JevCompactionPrepass(
        decision_provider=_Provider(low_confidence=True),
    )
    result = await prepass.classify(
        active_state={"current_request": "active"},
        units=[CompactionUnit("old", (0,), "user", "old detail")],
    )

    assert result.archive_indexes == frozenset()
    assert result.receipt["source"] == "fallback"
    assert result.receipt["fallback_reason"] == "low_confidence"
    assert result.usage is not None
    assert result.receipt["cost_usd"] == 0.0003


@pytest.mark.asyncio
async def test_one_uncertain_unit_does_not_veto_clear_archive() -> None:
    result = await JevCompactionPrepass(
        decision_provider=_MixedConfidenceProvider(),
    ).classify(
        active_state={"current_request": "active"},
        units=[
            CompactionUnit("old", (0,), "user", "old detail"),
            CompactionUnit("uncertain", (1,), "user", "possibly relevant detail"),
        ],
    )

    assert result.receipt["source"] == "jev"
    assert result.archive_indexes == frozenset({0})
    assert [item.retention for item in result.decisions] == ["archive", "retain"]
