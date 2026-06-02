"""Tests for coordinator worker definitions."""

from __future__ import annotations

from agent_driver.contracts import AgentProfile
from agent_driver.subagents import (
    apply_worker_tool_surface,
    default_worker_definitions,
    worker_definition_by_type,
)


def test_default_worker_definitions_include_phase_7_roles() -> None:
    """Built-in coordinator roles should be stable and addressable."""
    definitions = default_worker_definitions()
    worker_types = {definition.worker_type for definition in definitions}

    assert worker_types == {"worker", "researcher", "implementer", "verifier"}
    assert all(
        definition.profile == AgentProfile.REACT_TEXT for definition in definitions
    )
    assert all(definition.allowed_tools for definition in definitions)
    assert all(definition.handoff_rules for definition in definitions)


def test_worker_definition_lookup_is_normalized() -> None:
    """Lookup should accept user-facing case but return stable worker ids."""
    definition = worker_definition_by_type(" Researcher ")

    assert definition is not None
    assert definition.worker_type == "researcher"
    assert "url" in " ".join(definition.handoff_rules).lower()


def test_worker_tool_surface_intersects_parent_allowlist() -> None:
    """Worker roles should narrow child tools without broadening parent policy."""
    policy = apply_worker_tool_surface(
        parent_tool_policy={
            "allowed_tools": ["web_search", "web_fetch", "python"],
            "denied_tools": ["web_fetch"],
            "metadata": {"source": "parent"},
        },
        worker_type="researcher",
    )

    assert policy["allowed_tools"] == ["web_search"]
    assert policy["denied_tools"] == ["web_fetch"]
    assert policy["metadata"] == {
        "source": "parent",
        "worker_type": "researcher",
        "worker_allowed_tools": [
            "todo_write",
            "web_search",
            "web_fetch",
            "grep_search",
            "read_file",
        ],
        "worker_tool_surface": "role_restricted",
    }
