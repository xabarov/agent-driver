"""Harness defaults: timeout bump, continue-on-error, tool_packs tuple guard."""

from __future__ import annotations

import json

import pytest

from agent_driver.cli import evals as evals_module
from agent_driver.cli.evals import (
    EvalScenario,
    _is_transient_eval_error,
    _merge_eval_outputs,
    _write_scorecard,
    assert_eval_scenario_tool_packs_are_tuples,
    live_scenarios_for_suite,
    run_live_evaluation,
    summarize_run,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.cli.providers import (
    DEFAULT_LIVE_EVAL_TIMEOUT_S,
    CliProviderConfig,
    provider_config_for_eval,
)
from agent_driver.cli.tools import CliToolConfig
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.runtime import RuntimeStoreFactoryConfig


def test_provider_config_for_eval_bumps_default_timeout() -> None:
    config = CliProviderConfig(provider="openrouter", timeout_s=30.0)
    adjusted = provider_config_for_eval(config)
    assert adjusted.timeout_s == DEFAULT_LIVE_EVAL_TIMEOUT_S


def test_provider_config_for_eval_respects_explicit_timeout() -> None:
    config = CliProviderConfig(provider="openrouter", timeout_s=120.0)
    assert provider_config_for_eval(config).timeout_s == 120.0


def test_provider_config_for_eval_leaves_fake_unchanged() -> None:
    config = CliProviderConfig(provider="fake", timeout_s=30.0)
    assert provider_config_for_eval(config).timeout_s == 30.0


def test_tool_packs_must_be_tuple_not_str() -> None:
    bad = EvalScenario(scenario_id="bad", prompt="p", tool_packs=("filesystem_read"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="tool_packs must be a tuple"):
        assert_eval_scenario_tool_packs_are_tuples([bad])


def test_all_suites_use_tuple_tool_packs() -> None:
    for suite in ("default", "default_smoke", "deep", "regression"):
        assert_eval_scenario_tool_packs_are_tuples(live_scenarios_for_suite(suite))


def test_merge_eval_outputs_joins_turn_answers() -> None:
    first = AgentRunOutput.model_construct(
        run_id="r1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        answer="уточняющий вопрос",
        attempt_id="a1",
    )
    second = AgentRunOutput.model_construct(
        run_id="r2",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        attempt_id="a2",
        answer="описание def main",
    )
    merged = _merge_eval_outputs([first, second], base_run_id="run_base")
    assert "уточняющий" in (merged.answer or "")
    assert "def main" in (merged.answer or "")


def _event(
    seq: int, event_type: RuntimeEventType, payload: dict[str, object] | None = None
) -> RuntimeEvent:
    return RuntimeEvent.model_construct(
        event_id=f"evt_{seq}",
        type=event_type,
        run_id="run_eval_deep",
        attempt_id="attempt_1",
        seq=seq,
        created_at="2026-05-31T12:00:00Z",
        payload=payload or {},
    )


def test_eval_summary_embeds_deep_research_efficiency_diagnostics() -> None:
    output = AgentRunOutput.model_construct(
        run_id="run_eval_deep",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        attempt_id="attempt_1",
        answer="Готово.\n" + ("длинный дубль. " * 180),
        events=[
            _event(
                1,
                RuntimeEventType.TOOL_CALL_COMPLETED,
                {"tools": [{"tool_name": "web_search", "status": "completed"}]},
            ),
            _event(
                2,
                RuntimeEventType.ARTIFACT_UPDATED,
                {"path": "research/report.md", "operation": "write"},
            ),
            _event(
                3,
                RuntimeEventType.LLM_CALL_COMPLETED,
                {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 900,
                        "total_tokens": 1000,
                    }
                },
            ),
            _event(4, RuntimeEventType.RUN_COMPLETED, {}),
        ],
        tool_trace=[],
    )
    scenario = EvalScenario(
        scenario_id="deep_report",
        prompt="сделай deep research отчет",
        tags=("deep_research",),
    )

    summary = summarize_run(scenario=scenario, output=output, elapsed_ms=123)

    assert summary.llm_usage["total_tokens"] == 1000
    assert summary.research_efficiency["report_update_count"] == 1
    assert summary.research_efficiency["output_tokens_after_first_report_update"] == 900
    assert "deep_research_missing_initial_todo" in summary.bug_tags
    assert "deep_research_long_final_after_report" in summary.bug_tags
    assert summary.efficiency == "fail"


def test_eval_scorecard_renders_research_efficiency(tmp_path) -> None:
    summary = evals_module.EvalSummary(
        scenario_id="deep_report",
        run_id="run_1",
        status="completed",
        terminal_reason="final_answer",
        steps_total=4,
        llm_calls=1,
        tool_calls=3,
        tools_by_status={"completed": 3},
        tools_by_name_status={},
        repeated_tools=[],
        repeated_tool_arguments=[],
        empty_tool_results=0,
        interrupts_or_denials=0,
        answer_length=42,
        answer_language="ru",
        elapsed_ms=100,
        expected_tools_missing=[],
        forbidden_tools_used=[],
        answer_relevance="pass",
        tool_use_correctness="pass",
        efficiency="pass",
        notes="ok",
        bug_tags=["none"],
        actual_tool_chain=["todo_write", "web_search", "file_write"],
        llm_usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        research_efficiency={
            "deep_research_artifact_expected": True,
            "report_update_count": 1,
            "first_tool": "todo_write",
            "output_tokens_after_first_report_update": 20,
        },
    )

    _write_scorecard(target_dir=tmp_path, summaries=[summary], scenarios=[])

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "tool_chain: `todo_write -> web_search -> file_write`" in report
    assert "after_report=`20`" in report
    assert "artifact_expected=`True`" in report


@pytest.mark.asyncio
async def test_run_eval_scenario_with_retry_on_transient_error(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_DRIVER_RUN_LIVE_CLI_EVALS", "1")
    calls = {"count": 0}
    original = evals_module._run_eval_scenario

    async def _fail_once(**kwargs: object) -> object:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeExecutionError("LLM completion failed")
        return await original(**kwargs)

    monkeypatch.setattr(evals_module, "_run_eval_scenario", _fail_once)
    scenario = EvalScenario(scenario_id="ok", prompt="ok")
    bundle_dir, summaries = await run_live_evaluation(
        provider_config=CliProviderConfig(provider="fake"),
        tool_config=CliToolConfig(tools_mode="none"),
        store_config=RuntimeStoreFactoryConfig(kind="memory"),
        output_dir=tmp_path,
        scenarios=[scenario],
        offline=True,
    )
    assert calls["count"] == 2
    assert summaries[0].scenario_id == "ok"
    assert bundle_dir.exists()


@pytest.mark.asyncio
async def test_continue_on_error_writes_failures_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DRIVER_RUN_LIVE_CLI_EVALS", "1")
    calls = {"count": 0}
    original = evals_module._run_eval_scenario

    async def _fail_first(**kwargs: object) -> object:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return await original(**kwargs)

    monkeypatch.setattr(evals_module, "_run_eval_scenario", _fail_first)
    scenarios = [
        EvalScenario(scenario_id="will_fail", prompt="x"),
        EvalScenario(scenario_id="ok", prompt="ok"),
    ]
    bundle_dir, summaries = await run_live_evaluation(
        provider_config=CliProviderConfig(provider="fake"),
        tool_config=CliToolConfig(tools_mode="none"),
        store_config=RuntimeStoreFactoryConfig(kind="memory"),
        output_dir=tmp_path,
        scenarios=scenarios,
        offline=True,
        continue_on_error=True,
    )
    failures_path = bundle_dir / "failures.json"
    assert failures_path.exists()
    failures = json.loads(failures_path.read_text(encoding="utf-8"))
    assert failures[0]["scenario_id"] == "will_fail"
    assert len(summaries) == 1
    assert summaries[0].scenario_id == "ok"
