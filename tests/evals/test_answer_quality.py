"""Tests for the answer-quality layer: rubric evaluator + LLM judge (#5)."""

from __future__ import annotations

import pytest

from agent_driver.batch.contracts import Trajectory
from agent_driver.contracts import (
    AgentRunOutput,
    RuntimeEventType,
    new_runtime_event,
)
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.evals import (
    AnswerRubric,
    JudgeVerdict,
    LlmJudge,
    aggregate_trajectories,
    compare_aggregates,
    evaluate_answer_rubric,
    judge_trajectories,
    render_comparison,
)
from agent_driver.evals.judge import _normalize, _parse_verdict
from agent_driver.llm.providers_impl.fake import FakeProvider


def _output(answer: str | None) -> AgentRunOutput:
    events = [
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_q", "attempt_id": "att_q", "seq": 1},
        ),
        new_runtime_event(
            event_type=RuntimeEventType.RUN_COMPLETED,
            context={"run_id": "run_q", "attempt_id": "att_q", "seq": 2},
        ),
    ]
    return AgentRunOutput(
        run_id="run_q",
        attempt_id="att_q",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        answer=answer,
        events=events,
    )


# --------------------------------------------------------------------------- #
# Deterministic rubric evaluator                                              #
# --------------------------------------------------------------------------- #


def test_rubric_all_clauses_pass() -> None:
    out = _output("The total is 42 dollars, no errors.")
    result = evaluate_answer_rubric(
        out,
        rubric=AnswerRubric(
            must_contain=("42", "dollars"),
            must_not_contain=("failure",),
            regex=r"\d+ dollars",
        ),
    )
    assert result.passed
    assert result.score == 1.0
    assert result.details["regex_matched"] is True


def test_rubric_missing_required_fails_with_partial_score() -> None:
    out = _output("The total is 42.")
    result = evaluate_answer_rubric(
        out, rubric=AnswerRubric(must_contain=("42", "dollars"))
    )
    assert not result.passed
    assert result.score == pytest.approx(0.5)
    assert result.details["missing_required"] == ["dollars"]


def test_rubric_forbidden_literal_present_fails() -> None:
    out = _output("Sorry, I hit an error and cannot answer.")
    result = evaluate_answer_rubric(
        out, rubric=AnswerRubric(must_not_contain=("error",))
    )
    assert not result.passed
    assert result.details["forbidden_present"] == ["error"]


def test_rubric_case_insensitive_by_default() -> None:
    out = _output("RESULT: OK")
    assert evaluate_answer_rubric(
        out, rubric=AnswerRubric(must_contain=("result",))
    ).passed
    assert not evaluate_answer_rubric(
        out, rubric=AnswerRubric(must_contain=("result",), case_sensitive=True)
    ).passed


def test_rubric_empty_clauses_checks_nonempty_answer() -> None:
    assert evaluate_answer_rubric(_output("something"), rubric=AnswerRubric()).passed
    empty = evaluate_answer_rubric(_output("   "), rubric=AnswerRubric())
    assert not empty.passed
    assert empty.score == 0.0


def test_rubric_invalid_regex_is_a_failed_check_not_a_crash() -> None:
    out = _output("anything")
    result = evaluate_answer_rubric(out, rubric=AnswerRubric(regex=r"([unclosed"))
    assert not result.passed
    assert result.details["regex_matched"] is False


def test_rubric_none_answer_treated_as_empty() -> None:
    result = evaluate_answer_rubric(_output(None), rubric=AnswerRubric(must_contain=("x",)))
    assert not result.passed


# --------------------------------------------------------------------------- #
# Verdict parsing / normalization                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"score": 8, "rationale": "good"}', 0.8),
        ('noise {"score": 10} trailing', 1.0),
        ('{"score": 0.4}', 0.4),
        ("7", 0.7),
        ("garbage", 0.0),
    ],
)
def test_parse_verdict(raw: str, expected: float) -> None:
    assert _parse_verdict(raw).score == pytest.approx(expected)


def test_parse_verdict_unparseable_is_tagged() -> None:
    assert _parse_verdict("garbage").rationale == "unparseable-judge-reply"


def test_normalize_clamps_and_scales() -> None:
    assert _normalize(10.0) == 1.0
    assert _normalize(0.4) == 0.4
    assert _normalize(15.0) == 1.0
    assert _normalize(-3.0) == 0.0


# --------------------------------------------------------------------------- #
# LlmJudge over a fake provider                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_llm_judge_parses_provider_reply() -> None:
    judge = LlmJudge(
        provider=FakeProvider(response_text='{"score": 9, "rationale": "solid"}')
    )
    verdict = await judge.score(prompt="What is 6*7?", answer="42")
    assert verdict.score == pytest.approx(0.9)
    assert verdict.rationale == "solid"


@pytest.mark.asyncio
async def test_llm_judge_empty_answer_short_circuits() -> None:
    judge = LlmJudge(provider=FakeProvider(response_text='{"score": 9}'))
    verdict = await judge.score(prompt="q", answer="   ")
    assert verdict.score == 0.0
    assert verdict.rationale == "empty-answer"


class _BoomProvider:
    async def complete(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("provider down")

    async def stream(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("provider down")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_llm_judge_provider_error_yields_zero_verdict() -> None:
    judge = LlmJudge(provider=_BoomProvider())
    verdict = await judge.score(prompt="q", answer="an answer")
    assert verdict.score == 0.0
    assert verdict.rationale.startswith("judge-error:")


# --------------------------------------------------------------------------- #
# judge_trajectories -> aggregate -> compare quality delta                    #
# --------------------------------------------------------------------------- #


class _ConstJudge:
    def __init__(self, score: float) -> None:
        self._score = score

    async def score(self, *, prompt: str, answer: str) -> JudgeVerdict:  # noqa: D401
        return JudgeVerdict(score=self._score if answer else 0.0, rationale="const")


def _traj(item_id: str, answer: str | None, status: str = "completed") -> Trajectory:
    return Trajectory(
        item_id=item_id,
        run_id=f"rid-{item_id}",
        status=status,
        answer=answer,
        cost_usd=0.01,
        latency_ms=100.0,
        usage={"total_tokens": 50},
    )


@pytest.mark.asyncio
async def test_judge_trajectories_only_scores_answered_runs() -> None:
    runs = [_traj("a", "good answer"), _traj("b", None, status="error")]
    await judge_trajectories(runs, _ConstJudge(0.8), prompt_by_item={"a": "qa", "b": "qb"})
    assert runs[0].metadata["quality_score"] == pytest.approx(0.8)
    assert "quality_score" not in runs[1].metadata  # no answer → unscored


@pytest.mark.asyncio
async def test_quality_delta_flows_into_comparison_report() -> None:
    base = [_traj("a", "strong"), _traj("b", "strong")]
    treat = [_traj("a", "weak"), _traj("b", "weak")]
    await judge_trajectories(base, _ConstJudge(0.9))
    await judge_trajectories(treat, _ConstJudge(0.5))
    report = compare_aggregates(
        aggregate_trajectories(base), aggregate_trajectories(treat)
    )
    assert report.baseline.quality_score.median == pytest.approx(0.9)
    assert report.treatment.quality_score.median == pytest.approx(0.5)
    assert report.quality_score_median_delta == pytest.approx(-0.4)
    assert "quality (med)" in render_comparison(report)


def test_no_judge_hides_quality_row() -> None:
    agg = aggregate_trajectories([_traj("a", "x")])
    assert agg.quality_score.n == 0
    report = compare_aggregates(agg, agg)
    assert report.quality_score_median_delta == 0.0
    assert "quality (med)" not in render_comparison(report)
