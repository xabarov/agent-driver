"""Harness defaults: timeout bump, continue-on-error, tool_packs tuple guard."""

from __future__ import annotations

import json

import pytest

from agent_driver.cli import evals as evals_module
from agent_driver.cli.evals import (
    EvalScenario,
    _is_transient_eval_error,
    _merge_eval_outputs,
    assert_eval_scenario_tool_packs_are_tuples,
    live_scenarios_for_suite,
    run_live_evaluation,
)
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.cli.providers import (
    DEFAULT_LIVE_EVAL_TIMEOUT_S,
    CliProviderConfig,
    provider_config_for_eval,
)
from agent_driver.cli.tools import CliToolConfig
from agent_driver.contracts.enums import RunStatus, TerminalReason
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


@pytest.mark.asyncio
async def test_run_eval_scenario_with_retry_on_transient_error(tmp_path, monkeypatch) -> None:
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
