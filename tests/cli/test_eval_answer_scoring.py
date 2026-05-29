"""Tests for eval answer relevance scoring helpers."""

from __future__ import annotations

from agent_driver.cli.evals import (
    EvalScenario,
    _answer_matches_expectations,
    _is_transient_eval_error,
    summarize_run,
)
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.runtime.errors import RuntimeExecutionError
import httpx


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


def test_merge_joined_answer_satisfies_multi_turn_any_of() -> None:
    scenario = EvalScenario(
        scenario_id="ambiguous",
        prompt="p",
        expected_answer_any_of=(
            ("уточн", "clarif"),
            ("main.py", "def"),
        ),
    )
    merged = "Сначала уточню задачу.\n---\nВ main.py есть def cli_main"
    assert _answer_matches_expectations(answer=merged, scenario=scenario)


def test_is_transient_eval_error_detects_llm_and_timeout() -> None:
    assert _is_transient_eval_error(RuntimeExecutionError("LLM completion failed"))
    assert _is_transient_eval_error(httpx.ReadTimeout("read timed out"))
    assert not _is_transient_eval_error(ValueError("bad tool pack"))


def test_score_answer_last_turn_only_uses_follow_up_segment() -> None:
    scenario = EvalScenario(
        scenario_id="chat",
        prompt="p",
        score_answer_last_turn_only=True,
        expected_answer_any_of=(("def", "cli"),),
    )
    merged = "Файл найден: main.py\n---\nВ файле есть def main и cli entry"
    assert _answer_matches_expectations(
        answer=merged.rsplit("\n---\n", 1)[-1].strip(),
        scenario=scenario,
    )
    assert not _answer_matches_expectations(answer="Файл найден: main.py", scenario=scenario)


def test_sandbox_build_answer_from_bundle_matches_any_of() -> None:
    scenario = next(
        s
        for s in __import__(
            "agent_driver.cli.evals", fromlist=["default_deep_scenarios"]
        ).default_deep_scenarios()
        if s.scenario_id == "sandbox_build_verify"
    )
    answer = (
        "Завершено: оба файла созданы, тест пройден, содержимое `greet.py` и "
        "`test_greet.py` успешно прочитано."
    )
    assert _answer_matches_expectations(answer=answer, scenario=scenario)


def test_summarize_required_tools_last_turn_only() -> None:
    scenario = EvalScenario(
        scenario_id="chat",
        prompt="p",
        follow_up_prompts=("turn2",),
        required_tools=("read_file",),
        required_tools_last_turn_only=True,
    )
    output = AgentRunOutput.model_construct(
        run_id="r",
        attempt_id="a",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        tool_trace=[],
    )
    from agent_driver.contracts.tools import ToolTrace

    def _trace(step: int, name: str) -> ToolTrace:
        return ToolTrace(
            step=step,
            tool_name=name,
            status="completed",
            risk="low",
            side_effect="read_only",
            approval_mode="never",
        )

    output = output.model_copy(
        update={
            "tool_trace": [
                _trace(1, "todo_write"),
                _trace(2, "glob_search"),
                _trace(3, "todo_write"),
                _trace(4, "glob_search"),
            ]
        }
    )
    summary = summarize_run(scenario=scenario, output=output, elapsed_ms=1)
    assert summary.tool_use_correctness == "fail"
    output2 = output.model_copy(
        update={
            "tool_trace": output.tool_trace + [_trace(5, "read_file")]
        }
    )
    summary2 = summarize_run(scenario=scenario, output=output2, elapsed_ms=1)
    assert summary2.tool_use_correctness == "pass"


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
