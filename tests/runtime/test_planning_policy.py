"""Adaptive planning policy classifier tests."""

from __future__ import annotations

import pytest

from agent_driver.contracts.context import PlanningPolicyInput
from agent_driver.contracts.enums import PlanningHintLevel, SideEffectClass, ToolRisk
from agent_driver.contracts.tools import ToolCall, ToolManifest, ToolPolicyInput
from agent_driver.runtime.planning_policy import (
    classify_planning_hint,
    classify_planning_hint_for_tool_calls,
    tool_policy_with_planned_tool_hint,
)


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


def test_planning_policy_input_normalizes_metadata_mode() -> None:
    """Mode-only metadata should become an enabled typed planning policy."""
    policy = PlanningPolicyInput.from_metadata(
        {"force_planning": {"mode": "required_for_risky_tools"}}
    )
    assert policy is not None
    assert policy.enabled is True
    assert policy.mode.value == "required_for_risky_tools"


def test_planning_policy_input_keeps_legacy_enabled_flag() -> None:
    """Legacy force_planning_enabled metadata should remain supported."""
    policy = PlanningPolicyInput.from_metadata({"force_planning_enabled": True})
    assert policy is not None
    assert policy.enabled is True
    assert policy.mode.value == "required_for_writes"


def test_planning_policy_input_normalizes_tool_lists() -> None:
    """Blank tool names should not leak into policy evaluation."""
    policy = PlanningPolicyInput.model_validate(
        {"enabled": True, "gated_tools": [" agent_tool ", ""]}
    )
    assert policy.gated_tools == ["agent_tool"]


def test_classify_planning_hint_for_side_effecting_tool_batch() -> None:
    """Planned write/external tool batches should produce required hints."""
    manifests = {
        "file_write": ToolManifest(
            name="file_write",
            description="Write",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
        )
    }
    hint = classify_planning_hint_for_tool_calls(
        [ToolCall(tool_name="file_write")],
        manifest_lookup=manifests.get,
    )
    assert hint.level == PlanningHintLevel.REQUIRED
    assert "side_effecting_tool_planned" in hint.signals


def test_tool_policy_with_planned_tool_hint_preserves_existing_required_hint() -> None:
    """A weaker planned-tool hint should not downgrade existing metadata."""
    policy = ToolPolicyInput(
        metadata={
            "planning_hint": {
                "level": "required",
                "reason": "existing",
                "signals": ["side_effecting_tool_planned"],
            }
        }
    )
    next_policy = tool_policy_with_planned_tool_hint(
        policy,
        [ToolCall(tool_name="lookup")],
        manifest_lookup=lambda _name: ToolManifest(name="lookup", description="Lookup"),
    )
    assert next_policy.metadata["planning_hint"]["reason"] == "existing"


def test_planning_policy_input_can_enforce_required_hint() -> None:
    """Hosts can opt into using required planning hints as runtime policy."""
    policy = PlanningPolicyInput.from_metadata(
        {
            "planning_hint_enforce": True,
            "planning_hint": {
                "level": "required",
                "signals": ["subagent_spawn_requested"],
            },
        }
    )
    assert policy is not None
    assert policy.gated_tools == ["agent_tool"]
