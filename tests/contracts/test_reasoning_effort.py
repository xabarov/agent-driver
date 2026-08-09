"""R1 — abstract reasoning-effort tier: normalization + provider-neutral envelope."""

from __future__ import annotations

import pytest

from agent_driver.contracts.reasoning import (
    REASONING_EFFORT_TIERS,
    effort_to_reasoning_envelope,
    normalize_reasoning_effort,
)
from agent_driver.contracts.runtime import AgentRunInput


def test_ladder_vocabulary():
    assert REASONING_EFFORT_TIERS == (
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


@pytest.mark.parametrize(
    "raw,expected",
    [(None, None), ("", None), ("   ", None), ("HIGH", "high"), (" Max ", "max")],
)
def test_normalize(raw, expected):
    assert normalize_reasoning_effort(raw) == expected


def test_normalize_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_reasoning_effort("turbo")


@pytest.mark.parametrize(
    "tier,env",
    [
        (None, None),
        ("none", {"enabled": False}),
        ("minimal", {"effort": "minimal"}),
        ("low", {"effort": "low"}),
        ("high", {"effort": "high"}),
        ("max", {"effort": "max"}),
    ],
)
def test_envelope(tier, env):
    assert effort_to_reasoning_envelope(tier) == env


def test_agent_run_input_normalizes():
    ri = AgentRunInput(
        input="hi", agent_id="a", graph_preset="single_react", reasoning_effort=" High "
    )
    assert ri.reasoning_effort == "high"


def test_agent_run_input_rejects_unknown():
    with pytest.raises(Exception):
        AgentRunInput(
            input="hi",
            agent_id="a",
            graph_preset="single_react",
            reasoning_effort="bogus",
        )


def test_agent_run_input_default_is_none():
    ri = AgentRunInput(input="hi", agent_id="a", graph_preset="single_react")
    assert ri.reasoning_effort is None
