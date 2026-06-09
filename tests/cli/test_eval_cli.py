"""Tests for CLI evaluation harness helpers and commands."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
import json

import pytest

cli_main = importlib.import_module("agent_driver.cli.main")
from agent_driver.cli.evals import (
    EvalScenario,
    LiveEvalSkipped,
    can_run_provider,
    live_scenarios_for_suite,
    default_live_scenarios,
    render_eval_inspect,
    run_live_evaluation,
)
from agent_driver.cli.providers import CliProviderConfig
from agent_driver.contracts import AgentRunOutput
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.tools import ToolTrace
from agent_driver.cli.tools import CliToolConfig
from agent_driver.runtime import RuntimeStoreFactoryConfig


def test_default_live_scenarios_has_expected_count_and_diversity() -> None:
    """Scenario set should contain fixed 10 diverse items."""
    scenarios = default_live_scenarios()
    assert len(scenarios) == 10
    ids = {item.scenario_id for item in scenarios}
    assert len(ids) == 10
    assert "news_web_search" in ids
    assert "dangerous_tool_request" in ids


@pytest.mark.asyncio
async def test_run_live_evaluation_offline_writes_artifacts(tmp_path) -> None:
    """Offline eval mode should write summary/report/triage artifacts."""
    bundle_dir, summaries = await run_live_evaluation(
        provider_config=CliProviderConfig(provider="fake"),
        tool_config=CliToolConfig(tools_mode="default"),
        store_config=RuntimeStoreFactoryConfig(kind="memory"),
        output_dir=tmp_path,
        scenarios=[
            EvalScenario(
                scenario_id="offline_one",
                prompt="hello",
                max_steps=4,
                max_tool_calls=2,
                deadline_seconds=10.0,
            )
        ],
        offline=True,
    )
    assert bundle_dir.exists()
    assert summaries
    assert (bundle_dir / "summary.json").exists()
    assert (bundle_dir / "report.md").exists()
    assert (bundle_dir / "triage.json").exists()
    payload = json.loads((bundle_dir / "summary.json").read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload


def test_render_eval_inspect_is_deterministic() -> None:
    """Inspect renderer should emit stable plain output."""
    sample = {
        "scenario_id": "s1",
        "run_id": "r1",
        "status": "completed",
        "terminal_reason": "final_answer",
        "steps_total": 5,
        "llm_calls": 2,
        "tool_calls": 1,
        "tools_by_status": {"completed": 1},
        "tools_by_name_status": {"web_search": {"completed": 1}},
        "repeated_tools": [],
        "repeated_tool_arguments": [],
        "empty_tool_results": 0,
        "interrupts_or_denials": 0,
        "answer_length": 20,
        "answer_language": "ru",
        "elapsed_ms": 123,
        "expected_tools_missing": [],
        "forbidden_tools_used": [],
        "answer_relevance": "pass",
        "tool_use_correctness": "pass",
        "efficiency": "pass",
        "notes": "ok",
        "bug_tags": ["none"],
    }
    from agent_driver.cli.evals import EvalSummary

    text = render_eval_inspect(EvalSummary(**sample))
    assert "scenario> s1" in text
    assert "status> completed" in text


def test_eval_inspect_command_from_summary_file(tmp_path, capsys) -> None:
    """CLI eval inspect should render summary row content."""
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            [
                {
                    "scenario_id": "s1",
                    "run_id": "r1",
                    "status": "completed",
                    "terminal_reason": "final_answer",
                    "steps_total": 5,
                    "llm_calls": 2,
                    "tool_calls": 1,
                    "tools_by_status": {"completed": 1},
                    "tools_by_name_status": {"web_search": {"completed": 1}},
                    "repeated_tools": [],
                    "repeated_tool_arguments": [],
                    "empty_tool_results": 0,
                    "interrupts_or_denials": 0,
                    "answer_length": 20,
                    "answer_language": "ru",
                    "elapsed_ms": 123,
                    "expected_tools_missing": [],
                    "forbidden_tools_used": [],
                    "answer_relevance": "pass",
                    "tool_use_correctness": "pass",
                    "efficiency": "pass",
                    "notes": "ok",
                    "bug_tags": ["none"],
                }
            ]
        ),
        encoding="utf-8",
    )
    code = cli_main.main(["eval", "inspect", "--summary-json", str(summary_path)])
    assert code == 0
    output = capsys.readouterr().out
    assert "scenario> s1" in output
    assert "status> completed" in output


def test_eval_inspect_command_from_artifact_file(tmp_path, capsys) -> None:
    """CLI eval inspect should render timeline from artifact json."""
    artifact_path = tmp_path / "scenario.json"
    artifact_path.write_text(
        json.dumps(
            {
                "scenario": {"scenario_id": "s1"},
                "summary": {"status": "completed", "terminal_reason": "final_answer"},
                "event_replay": [{"seq": 1, "type": "run_started"}],
                "tool_trace": [],
                "terminal": {"status": "completed", "reason": "final_answer"},
                "final_answer": "ok",
            }
        ),
        encoding="utf-8",
    )
    code = cli_main.main(["eval", "inspect", "--artifact-json", str(artifact_path)])
    assert code == 0
    output = capsys.readouterr().out
    assert "event> seq=1 type=run_started" in output
    assert "final_answer_len> 2" in output


def test_eval_inspect_requires_exactly_one_input(capsys) -> None:
    """CLI should reject inspect call without required input selector."""
    code = cli_main.main(["eval", "inspect"])
    assert code == 2
    output = capsys.readouterr().out
    assert "exactly one" in output


def test_eval_run_command_dispatch_monkeypatched(monkeypatch) -> None:
    """Main should dispatch eval run command."""

    async def _fake_eval_run(_args):
        return 0

    monkeypatch.setattr(cli_main, "_eval_run_command", _fake_eval_run)
    assert cli_main.main(["eval", "run", "--provider", "fake", "--offline"]) == 0


def test_eval_run_command_dispatch_supports_regression_suite(monkeypatch) -> None:
    """Main should accept regression suite selector for eval run."""

    async def _fake_eval_run(_args):
        return 0

    monkeypatch.setattr(cli_main, "_eval_run_command", _fake_eval_run)
    assert (
        cli_main.main(
            ["eval", "run", "--provider", "fake", "--offline", "--suite", "regression"]
        )
        == 0
    )


def test_regression_suite_contains_new_guard_scenarios() -> None:
    """Regression suite should include parser/glob/web resilience checks."""
    ids = {item.scenario_id for item in live_scenarios_for_suite("regression")}
    assert "qwen_text_form_tool_call" in ids
    assert "glob_root_listing" in ids
    assert "web_search_upstream_error" in ids
    assert "stale_knowledge_sam" in ids
    assert "stale_knowledge_sam_offline" in ids
    assert "repo_topfiles_no_recursion" in ids
    assert "denial_no_retry" in ids
    assert "denial_no_retry_offline" in ids


def test_can_run_provider_reports_missing_openrouter_config(monkeypatch) -> None:
    """Live eval readiness check should explain missing OpenRouter config."""
    monkeypatch.delenv("AGENT_DRIVER_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_MODEL", raising=False)
    monkeypatch.delenv("AGENT_DRIVER_API_KEY", raising=False)
    ok, reason = can_run_provider(CliProviderConfig(provider="openrouter"))
    assert ok is False
    assert reason is not None and "not fully configured" in reason


@pytest.mark.asyncio
async def test_live_eval_gate_raises_skip_without_opt_in(tmp_path, monkeypatch) -> None:
    """Without opt-in env and offline flag, run should skip."""
    monkeypatch.delenv("AGENT_DRIVER_RUN_LIVE_CLI_EVALS", raising=False)
    with pytest.raises(LiveEvalSkipped):
        await run_live_evaluation(
            provider_config=CliProviderConfig(provider="fake"),
            tool_config=CliToolConfig(tools_mode="default"),
            store_config=RuntimeStoreFactoryConfig(kind="memory"),
            output_dir=tmp_path,
            scenarios=[EvalScenario(scenario_id="s1", prompt="hello")],
            offline=False,
        )


def test_eval_run_command_clean_skip_without_opt_in(monkeypatch, capsys) -> None:
    """Top-level eval run should exit cleanly on gate skip."""
    monkeypatch.delenv("AGENT_DRIVER_RUN_LIVE_CLI_EVALS", raising=False)
    code = cli_main.main(["eval", "run", "--provider", "fake"])
    assert code == 0
    output = capsys.readouterr().out
    assert "eval skip:" in output


@pytest.mark.asyncio
async def test_run_live_evaluation_writes_absolute_sandbox_dir(tmp_path) -> None:
    """Sandbox scenario artifact should persist absolute sandbox path."""
    bundle_dir, _ = await run_live_evaluation(
        provider_config=CliProviderConfig(provider="fake"),
        tool_config=CliToolConfig(tools_mode="default"),
        store_config=RuntimeStoreFactoryConfig(kind="memory"),
        output_dir=tmp_path,
        scenarios=[
            EvalScenario(
                scenario_id="sandbox_case",
                prompt="x",
                prompt_template="work in {sandbox}",
                sandbox_required=True,
                max_steps=4,
                max_tool_calls=2,
                deadline_seconds=10.0,
            )
        ],
        offline=True,
    )
    artifact = json.loads(
        (bundle_dir / "sandbox_case.json").read_text(encoding="utf-8")
    )
    sandbox_raw = artifact["scenario"]["sandbox_dir"]
    assert isinstance(sandbox_raw, str)
    assert Path(sandbox_raw).is_absolute()


def test_summarize_run_uses_tool_results_args_for_repeated_arguments() -> None:
    """repeated_tool_arguments should use call.args when available."""
    from agent_driver.cli.evals import summarize_run

    scenario = EvalScenario(scenario_id="s", prompt="p", expected_tools=("file_write",))
    event = RuntimeEvent(
        event_id="evt_1",
        type="run_completed",
        run_id="run_s",
        attempt_id="attempt_1",
        seq=1,
        created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        payload={},
    )
    trace_1 = ToolTrace(
        step=1,
        tool_name="file_write",
        status="completed",
        risk="medium",
        side_effect="reversible_write",
        approval_mode="on_policy_match",
    )
    trace_2 = ToolTrace(
        step=2,
        tool_name="file_write",
        status="completed",
        risk="medium",
        side_effect="reversible_write",
        approval_mode="on_policy_match",
    )
    output = AgentRunOutput(
        run_id="run_s",
        attempt_id="attempt_1",
        status="completed",
        terminal_reason="final_answer",
        answer="ok",
        events=[event],
        tool_trace=[trace_1, trace_2],
        metadata={
            "tool_results": [
                {"call": {"tool_name": "file_write", "args": {"path": "/tmp/a.txt"}}},
                {"call": {"tool_name": "file_write", "args": {"path": "/tmp/b.txt"}}},
            ],
            "step_count": 5,
        },
    )
    summary = summarize_run(scenario=scenario, output=output, elapsed_ms=10)
    assert summary.repeated_tools == ["file_write"]
    assert summary.repeated_tool_arguments == []
    assert summary.runtime_step_count == 5


@pytest.mark.asyncio
async def test_run_live_evaluation_offline_interrupt_resume(tmp_path) -> None:
    scenario = next(
        row
        for row in live_scenarios_for_suite("regression")
        if row.scenario_id == "interrupt_resume_file_write"
    )
    bundle_dir, summaries = await run_live_evaluation(
        provider_config=CliProviderConfig(provider="fake"),
        tool_config=CliToolConfig(tools_mode="none"),
        store_config=RuntimeStoreFactoryConfig(kind="memory"),
        output_dir=tmp_path,
        scenarios=[scenario],
        offline=True,
    )
    assert summaries
    assert summaries[0].scenario_id == "interrupt_resume_file_write"
    assert summaries[0].tool_use_correctness == "pass"
    assert (bundle_dir / "interrupt_resume_file_write.json").exists()


@pytest.mark.asyncio
async def test_run_live_evaluation_offline_smoke_file_edit_minimal_patch(
    tmp_path,
) -> None:
    """Deep file_edit scenario should render sandbox artifact in offline mode."""
    scenario = next(
        row
        for row in live_scenarios_for_suite("deep")
        if row.scenario_id == "file_edit_minimal_patch"
    )
    bundle_dir, _ = await run_live_evaluation(
        provider_config=CliProviderConfig(provider="fake"),
        tool_config=CliToolConfig(tools_mode="default"),
        store_config=RuntimeStoreFactoryConfig(kind="memory"),
        output_dir=tmp_path,
        scenarios=[scenario],
        offline=True,
    )
    artifact = json.loads(
        (bundle_dir / "file_edit_minimal_patch.json").read_text(encoding="utf-8")
    )
    assert artifact["scenario"]["sandbox_required"] is True
    assert artifact["scenario"]["scenario_id"] == "file_edit_minimal_patch"


def test_eval_compare_offline_runs_and_reports(capsys) -> None:
    """`eval compare --offline` runs the general suite deterministically."""
    code = cli_main.main(
        ["eval", "compare", "--offline", "--repeats", "2", "--concurrency", "2"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "prompt_cache_off" in out and "prompt_cache_on" in out
    assert "success_rate" in out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["axis"] == "prompt_cache"
    assert payload["offline"] is True
    assert payload["repeats"] == 2
