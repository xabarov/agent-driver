"""Adaptive planning policy classifier tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import PlanningHintLevel
from agent_driver.runtime.planning_policy import classify_planning_hint


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Explain how plan mode works in this project", PlanningHintLevel.NONE),
        ("исправь опечатку в README", PlanningHintLevel.NONE),
        (
            "Add a new force planning policy and wire tests across runtime files",
            PlanningHintLevel.SUGGESTED,
        ),
        (
            "реализуй поддержку subagents и обнови несколько runtime модулей",
            PlanningHintLevel.SUGGESTED,
        ),
        ("составь план работ по steering control plane", PlanningHintLevel.SUGGESTED),
    ],
)
def test_classify_planning_hint_from_user_message(
    message: str, expected: PlanningHintLevel
) -> None:
    """Common English/Russian phrasing should map to stable hint levels."""
    assert classify_planning_hint(message).level == expected


def test_classify_planning_hint_requires_for_runtime_boundaries() -> None:
    """Runtime-known risky execution boundaries should force planning."""
    hint = classify_planning_hint(
        "run the next step",
        side_effecting_tool_planned=True,
    )
    assert hint.level == PlanningHintLevel.REQUIRED
    assert "side_effecting_tool_planned" in hint.signals


def test_classify_planning_hint_expected_steps_threshold() -> None:
    """A known multi-step execution estimate should require a plan."""
    hint = classify_planning_hint("continue", expected_steps=4)
    assert hint.level == PlanningHintLevel.REQUIRED
    assert "expected_steps_ge_4" in hint.signals
