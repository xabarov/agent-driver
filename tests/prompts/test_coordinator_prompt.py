"""Coordinator prompt snapshot tests."""

from __future__ import annotations

from agent_driver.prompts import coordinator_system_prompt


def test_coordinator_prompt_snapshot_contains_openclaude_principles() -> None:
    """Coordinator prompt should pin worker lifecycle principles."""
    prompt = coordinator_system_prompt()

    assert "Do not pretend worker results arrived" in prompt
    assert "Use existing workers" in prompt
    assert "self-contained task" in prompt
    assert "independent verifier" in prompt
    assert "Stop or correct workers" in prompt
