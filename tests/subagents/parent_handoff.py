"""Test helpers for subagent parent handoff construction."""

from __future__ import annotations

from agent_driver.subagents.handoff import SubagentParentHandoff


def default_parent_handoff(**overrides: object) -> SubagentParentHandoff:
    """Build a parent handoff with sensible defaults for unit tests."""
    base = {
        "run_id": "run_parent",
        "attempt_id": "att_parent",
        "thread_id": None,
        "agent_id": "agent.parent",
        "graph_preset": "single_react",
        "model_role": "default",
        "tool_policy": {},
        "answer": None,
        "artifact_refs": [],
        "digest_refs": [],
        "planning_state": None,
    }
    base.update(overrides)
    return SubagentParentHandoff(**base)  # type: ignore[arg-type]


__all__ = ["default_parent_handoff"]
