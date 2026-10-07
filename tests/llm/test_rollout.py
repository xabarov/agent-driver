"""Stage 5 rollout policy and automatic rollback tests."""

from __future__ import annotations

import pytest

from agent_driver.llm.rollout import (
    JevRolloutController,
    JevRolloutSettings,
    resolve_jev_mode,
)


def test_rollout_settings_resolve_task_and_gate_modes() -> None:
    policy = JevRolloutSettings(
        mode="shadow",
        gate_modes={"memory": "active"},
        task_modes={"low_risk": "active"},
        task_allowlist=("low_risk", "review"),
        pinned_models={"routing": "typesafe/jev-1.13"},
    )

    assert policy.mode_for("quality", task="review") == "shadow"
    assert policy.mode_for("memory", task="review") == "active"
    assert policy.mode_for("routing", task="low_risk") == "active"
    assert policy.mode_for("routing", task="normal_chat") == "off"
    assert policy.model_for("routing") == "typesafe/jev-1.13"
    assert policy.metadata()["schema"] == "jev-rollout.v1"


def test_controller_rolls_back_after_fallback_breach() -> None:
    controller = JevRolloutController(
        JevRolloutSettings(
            mode="active",
            max_fallback_rate=0.25,
            min_observations_for_rollback=4,
        )
    )
    for index in range(4):
        controller.observe(gate="routing", fallback=index < 2)

    status = controller.status(gate="routing")
    assert status["rolled_back"] is True
    assert status["rollback_reason"] == "fallback_rate_limit"
    assert controller.mode_for("routing", task="normal_chat") == "off"


def test_missing_policy_is_legacy_active_and_malformed_policy_is_off() -> None:
    assert resolve_jev_mode(None, "quality", task="normal_chat") == "active"
    assert resolve_jev_mode(object(), "quality", task="normal_chat") == "off"


def test_settings_reject_invalid_modes() -> None:
    with pytest.raises(ValueError):
        JevRolloutSettings(mode="launch")  # type: ignore[arg-type]


def test_controller_rolls_back_after_latency_p95_breach() -> None:
    controller = JevRolloutController(
        JevRolloutSettings(
            mode="active",
            max_latency_p95_ms=100.0,
            min_observations_for_rollback=2,
        )
    )
    controller.observe(gate="routing", latency_ms=10.0)
    controller.observe(gate="routing", latency_ms=250.0)

    status = controller.status(gate="routing")
    assert status["latency_p95_ms"] == 250.0
    assert status["rolled_back"] is True
    assert status["rollback_reason"] == "latency_p95_limit"
