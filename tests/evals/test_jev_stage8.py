from pathlib import Path

import pytest

from agent_driver.evals.jev_stage8 import (
    PromotionSettings,
    evaluate_production_promotion,
)
from agent_driver.llm import JevOutcomeLabel, JevProductionLabelLedger


def _labels(*, all_gates: bool = False) -> list[JevOutcomeLabel]:
    gates = ("routing", "quality", "compaction", "memory") if all_gates else ("routing", "quality")
    return [
        JevOutcomeLabel(
            label_id=f"label-{gate}-{window}-{index}",
            decision_id=f"decision-{gate}-{window}-{index}",
            gate=gate,
            task="normal_chat",
            correct=True,
            safety_passed=True,
            fallback=False,
            latency_ms=800.0,
            cost_usd=0.00003,
            window_id=window,
        )
        for gate in gates
        for window in ("w1", "w2")
        for index in range(5)
    ]


def test_label_ledger_is_deduplicated_and_raw_free(tmp_path: Path) -> None:
    ledger = JevProductionLabelLedger(tmp_path / "labels.jsonl")
    label = _labels()[0]

    assert ledger.append(label) is True
    assert ledger.append(label) is False
    assert ledger.read()[0].label_id == label.label_id
    with pytest.raises(ValueError):
        JevOutcomeLabel.from_dict({**label.to_dict(), "prompt": "secret"})


def test_empty_production_evidence_stays_in_shadow() -> None:
    report = evaluate_production_promotion(
        [], stage7_report={"promotion": {"passed": True}}
    )

    assert report["promotion"]["stage"] == "shadow"
    assert report["promotion"]["recommendation"] == "collect_production_labels"
    assert "no_production_labels" in report["promotion"]["reasons"]


def test_two_green_windows_promote_only_normal_chat_canary() -> None:
    report = evaluate_production_promotion(
        _labels(), stage7_report={"schema": "jev-stage7-latency-calibration.v1", "promotion": {"passed": True}}
    )

    assert report["promotion"]["stage"] == "normal_chat_canary"
    assert report["promotion"]["active_gates"] == ["routing", "quality"]
    assert report["promotion"]["held_gates"] == ["compaction", "memory"]
    assert report["promotion"]["default_on_enabled"] is False


def test_default_on_requires_all_gate_labels_and_operator_approval() -> None:
    labels = _labels(all_gates=True)
    settings = PromotionSettings(operator_approved_default_on=True)
    report = evaluate_production_promotion(
        labels,
        stage7_report={"promotion": {"passed": True}},
        settings=settings,
    )

    assert report["promotion"]["stage"] == "default_on"
    assert report["promotion"]["default_on_enabled"] is True
    assert report["promotion"]["active_gates"] == [
        "routing", "quality", "compaction", "memory"
    ]
