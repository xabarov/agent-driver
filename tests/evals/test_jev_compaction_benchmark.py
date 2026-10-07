"""Synthetic JEV compaction benchmark invariants."""

from __future__ import annotations

import pytest

from agent_driver.evals.jev_compaction_benchmark import compare_rows, run_case
from agent_driver.evals.jev_compaction_scenarios import synthetic_compaction_scenarios


@pytest.mark.asyncio
async def test_oracle_prepass_reduces_input_and_preserves_protected_facts() -> None:
    scenario = synthetic_compaction_scenarios()[0]
    baseline = await run_case(scenario, mode="baseline")
    oracle = await run_case(scenario, mode="oracle_stub")

    compare_rows([baseline, oracle])
    assert baseline["compaction_success"] is True
    assert oracle["compaction_success"] is True
    assert oracle["compactor_input_reduction_pct"] > 50
    assert oracle["protected_fact_recall"] == 1.0
    assert oracle["archive_precision"] == 1.0
    assert oracle["archive_recall"] == 1.0
    assert oracle["durable_transcript_unchanged"] is True
    assert oracle["same_compactor_input_as_baseline"] is False


@pytest.mark.asyncio
async def test_forced_fallback_keeps_baseline_compactor_input() -> None:
    scenario = synthetic_compaction_scenarios()[0]
    baseline = await run_case(scenario, mode="baseline")
    fallback = await run_case(scenario, mode="forced_fallback")

    compare_rows([baseline, fallback])
    assert fallback["prepass"]["fallback_reason"] == "DecisionTimeoutError"
    assert fallback["archived_messages"] == 0
    assert fallback["same_compactor_input_as_baseline"] is True
    assert fallback["protected_fact_recall"] == 1.0


@pytest.mark.asyncio
async def test_no_pressure_control_makes_no_decision_call() -> None:
    scenario = next(
        item for item in synthetic_compaction_scenarios() if item.name == "no_pressure_control"
    )
    result = await run_case(scenario, mode="oracle_stub")

    assert result["eligible"] is False
    assert result["decision_calls"] == 0
    assert result["summary_calls"] == 0
