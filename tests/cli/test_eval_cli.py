"""Tests for CLI evaluation harness helpers and commands."""

from __future__ import annotations

import importlib
import json

import pytest

cli_main = importlib.import_module("agent_driver.cli.main")
from agent_driver.cli.evals import (
    EvalScenario,
    LiveEvalSkipped,
    can_run_provider,
    default_live_scenarios,
    render_eval_inspect,
    run_live_evaluation,
)
from agent_driver.cli.providers import CliProviderConfig
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
