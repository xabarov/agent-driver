"""Stage 6 canary gates and calibration tests."""

from agent_driver.evals.jev_stage6 import (
    JevCanarySettings,
    evaluate_stage6_canary,
)


def _report(*, passed: bool = True, latency: float = 120.0) -> dict:
    rows = []
    for repeat in range(2):
        for case_id, surface, outcome, confidence in (
            ("routing.simple", "routing", "fast", 0.91),
            ("quality.grounded", "quality", "accept", 0.88),
        ):
            invariant = {"safe": True}
            active = {
                "outcome": outcome,
                "decision_correct": passed,
                "fallback": False,
                "confidence": confidence,
                "source": "jev",
                "invariants": invariant,
            }
            rows.append({
                "case_id": case_id,
                "repeat": repeat,
                "surface": surface,
                "categories": [surface],
                "treatments": [
                    {"mode": "off", "effect": {}},
                    {"mode": "shadow", "effect": {}},
                    active,
                ],
                "call": {"latency_ms": latency, "cost_usd": 0.00001},
                "shadow_behavior_unchanged": True,
                "active_effect_observed": True,
                "paired_replay_valid": True,
            })
    return {
        "schema": "jev-live-validation.v1",
        "live": True,
        "passed": passed,
        "decision_model": "typesafe/jev-1.13",
        "rows": rows,
    }


def test_stage6_promotes_active_low_risk_gates_and_calibrates() -> None:
    report = evaluate_stage6_canary(
        _report(),
        stage5_report={"rollout": {"active_low_risk_ready": True}},
        chat_demo_check={"passed": True, "raw_free": True},
    )

    assert report["promotion"]["passed"] is True
    assert report["promotion"]["recommendation"] == "canary_active"
    assert report["promotion"]["active_gates"] == ["routing", "quality"]
    assert report["promotion"]["held_gates"] == ["compaction", "memory"]
    assert report["calibration"]["gates"]["routing"]["samples"] == 2
    assert report["checks"]["latency_p95"] is True


def test_stage6_holds_and_requests_rollback_on_latency() -> None:
    report = evaluate_stage6_canary(
        _report(latency=9000.0),
        settings=JevCanarySettings(max_latency_p95_ms=500.0),
    )

    assert report["promotion"]["passed"] is False
    assert report["promotion"]["recommendation"] == "shadow"
    assert report["promotion"]["rollback_required"] is True
    assert "latency_p95" in report["promotion"]["reasons"]


def test_canary_settings_build_task_scoped_runtime_policy() -> None:
    settings = JevCanarySettings()
    policy = settings.rollout_settings("typesafe/jev-1.13")

    assert policy.mode_for("routing", task="normal_chat") == "active"
    assert policy.mode_for("quality", task="normal_chat") == "active"
    assert policy.mode_for("compaction", task="normal_chat") == "shadow"
    assert policy.mode_for("routing", task="other") == "off"
    assert policy.metadata()["max_latency_p95_ms"] == 5000.0
