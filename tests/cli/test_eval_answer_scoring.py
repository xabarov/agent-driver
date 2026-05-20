"""Tests for eval answer relevance scoring helpers."""

from __future__ import annotations

from agent_driver.cli.evals import EvalScenario, _answer_matches_expectations


def test_answer_matches_any_of_russian_or_english() -> None:
    scenario = EvalScenario(
        scenario_id="loop",
        prompt="p",
        expected_answer_any_of=(("not found", "не найдено"),),
    )
    assert _answer_matches_expectations(
        answer="Совпадений не найдено.", scenario=scenario
    )
    assert _answer_matches_expectations(answer="Token not found.", scenario=scenario)


def test_summarize_run_passes_with_any_of_groups() -> None:
    scenario = EvalScenario(
        scenario_id="web_zero",
        prompt="p",
        expected_answer_any_of=(("no results", "ничего не найден"),),
    )
    assert _answer_matches_expectations(
        answer="По запросу ничего не найдено.",
        scenario=scenario,
    )
