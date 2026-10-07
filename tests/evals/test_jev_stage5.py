"""Stage 5 replay corpus, calibration, and report invariants."""

from __future__ import annotations

import pytest

from agent_driver.evals.jev_stage5 import (
    build_stage5_replay_corpus,
    calibrate_jev_thresholds,
    evaluate_stage5_rows,
    run_stage5_offline_replay,
    stage5_calibration_fixture,
)


def test_stage5_corpus_covers_all_rollout_surfaces() -> None:
    categories = {item.category for item in build_stage5_replay_corpus()}
    assert {
        "routing",
        "escalation",
        "tool_safety",
        "compaction",
        "corrections",
        "memory",
        "multilingual",
        "long_tool_history",
    } <= categories


def test_thresholds_are_calibrated_per_gate() -> None:
    result = calibrate_jev_thresholds(stage5_calibration_fixture())

    assert set(result) == {"routing", "quality", "compaction", "memory"}
    assert len({item["threshold"] for item in result.values()}) > 1
    assert all(item["status"] == "calibrated" for item in result.values())


def test_stage5_report_holds_default_on_without_live_chat_evidence() -> None:
    rows = [
        {
            "scenario": "topic_switch",
            "mode": "oracle_stub",
            "repeat": 0,
            "live": False,
            "compaction_success": True,
            "compactor_input_reduction_pct": 80.0,
            "protected_fact_recall": 1.0,
            "context_fact_recall": 1.0,
            "total_latency_ms": 10.0,
            "total_cost_usd": 0.0,
            "prepass": {"source": "jev"},
        }
    ]
    report = evaluate_stage5_rows(rows)

    assert report["rollout"]["replay_green"] is True
    assert report["rollout"]["default_on_ready"] is False
    assert report["rollout"]["recommendation"] == "shadow"
    assert "candidate_answer" not in str(report)


def test_stage5_merges_cross_surface_live_validation_coverage() -> None:
    rows = [
        {
            "scenario": "topic_switch",
            "mode": "jev",
            "live": True,
            "compaction_success": True,
            "protected_fact_recall": 1.0,
            "prepass": {"source": "jev"},
        }
    ]
    validation = {
        "schema": "jev-live-validation.v1",
        "live": True,
        "categories": ["routing", "escalation", "tool_safety", "memory"],
        "metrics": {"shadow_behavior_unchanged": True},
        "rows": [
            {"category": category, "mode": "active"}
            for category in validation_categories()
        ],
    }
    report = evaluate_stage5_rows(rows, live=True, live_validation=validation)

    assert report["live_validation"]["schema"] == "jev-live-validation.v1"
    assert "routing" not in report["rollout"]["coverage_gaps"]


def test_stage5_records_chat_demo_evidence_without_enabling_global_default() -> None:
    report = evaluate_stage5_rows(
        [],
        live_validation={
            "schema": "jev-live-validation.v1",
            "live": True,
            "rows": [],
            "categories": [],
            "metrics": {},
        },
        chat_demo_check={
            "schema": "jev-chat-demo-live-check.v1",
            "passed": True,
            "raw_free": True,
        },
    )

    assert report["chat_demo_check"]["passed"] is True
    assert report["rollout"]["chat_demo_check_passed"] is True
    assert report["rollout"]["default_on_ready"] is False


def validation_categories() -> tuple[str, ...]:
    return (
        "routing",
        "escalation",
        "tool_safety",
        "memory",
        "corrections",
        "multilingual",
    )


@pytest.mark.asyncio
async def test_offline_stage5_replay_produces_green_compaction_evidence() -> None:
    report = await run_stage5_offline_replay(repeats=1)

    assert report["corpus"]["cases"] >= 10
    assert report["metrics"]["treatment_rows"] == 5
    assert report["metrics"]["protected_fact_recall"] == 1.0
    assert report["rollout"]["default_on_ready"] is False
