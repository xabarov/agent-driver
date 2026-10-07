"""Rollout evidence uses runtime side effects and fails closed on regressions."""
from __future__ import annotations

import json

import pytest

from agent_driver.evals.jev_live_validation import CASES, run_live_validation
from agent_driver.llm.decision_contracts import DecisionAnswer, DecisionResponse, DecisionTimeoutError


class DecisionStub:
    def __init__(self, *, timeout=False, bad_memory=False, bad_quality=False):
        self.timeout = timeout
        self.bad_memory = bad_memory
        self.bad_quality = bad_quality
        self.calls = 0
        self.states = []

    async def decide(self, *, state, questions, model=None):
        self.calls += 1
        self.states.append(state)
        if self.timeout:
            raise DecisionTimeoutError()
        answers = {}
        request = state.get("request", "")
        for key, question in questions.items():
            if question.type == "choice":
                labels = list(question.criteria)
                if key == "tier":
                    selected = "strong" if "Докажи" in request else "fast"
                elif key == "next_action":
                    selected = "ask_user" if "документ" in request else "escalate_strong" if "primes" in request else "finalize"
                    if self.bad_quality:
                        selected = "finalize"
                elif key == "repair_action":
                    selected = "accept"
                else:
                    index = int(key.split("_")[1])
                    selected = ("durable", "durable" if self.bad_memory else "session_only", "sensitive", "durable")[index]
                answers[key] = DecisionAnswer(
                    question_id=key, type="choice", choice=selected, confidence=0.98,
                    probabilities={label: 0.98 if label == selected else 0.02 / (len(labels) - 1) for label in labels},
                )
            else:
                value = 0.01
                if key == "requires_strong" and "Докажи" in request:
                    value = 0.99
                elif key in {"sufficient", "grounded"}:
                    value = 0.99 if "2 + 2" in request or self.bad_quality else 0.01
                elif key == "candidate_3_contradiction":
                    value = 0.99
                answers[key] = DecisionAnswer(question_id=key, type="noul", noul=value)
        return DecisionResponse(model=model or "stub/jev", answers=answers,
                                input_tokens=10, output_tokens=5, cost_usd=0.001)


@pytest.mark.asyncio
async def test_hooks_measure_paired_effects_without_double_billing():
    provider = DecisionStub()
    report = await run_live_validation(decision_provider=provider)
    m = report["metrics"]
    assert provider.calls == len(CASES) == m["decision_calls"]
    assert m["cost_usd"] == pytest.approx(len(CASES) * 0.001)
    assert m["shadow_behavior_unchanged"] and m["paired_replay_valid"]
    assert m["invariants_passed"] and m["rollback_check"]
    assert report["passed"]
    assert report["live"] is False
    memory = next(row for row in report["rows"] if row["surface"] == "memory")
    assert [item["effect"]["writes"] for item in memory["treatments"]] == [0, 0, 1]
    assert "synthetic-validation-secret" not in json.dumps(provider.states)
    serialized = json.dumps(report)
    assert all(case.request not in serialized for case in CASES)
    assert "API key:" not in serialized
    assert all(row["treatments"][0]["controller_observations"] == 0 for row in report["rows"])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "bad_memory", "bad_quality"])
async def test_failed_decisions_cannot_be_reported_as_pass(failure):
    report = await run_live_validation(decision_provider=DecisionStub(**{failure: True}))
    assert not report["passed"]
    if failure == "timeout":
        assert report["metrics"]["cost_usd"] is None
        assert report["metrics"]["fallback_rate"] == 1.0
    elif failure == "bad_memory":
        assert not report["metrics"]["invariants_passed"]
    else:
        assert report["metrics"]["decision_accuracy"] < 1.0
