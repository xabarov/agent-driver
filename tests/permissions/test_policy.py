"""Tests for the declarative permission policy."""

from __future__ import annotations

import pytest

from agent_driver.permissions import (
    PermissionDecision,
    PermissionMode,
    PermissionPolicy,
    PermissionRule,
)


def _decide(policy: PermissionPolicy, command: str, tool: str = "bash"):
    return policy.decide(tool, {"command": command})


def test_standard_mode_denies_critical_asks_dangerous() -> None:
    policy = PermissionPolicy(mode=PermissionMode.STANDARD)
    assert _decide(policy, "rm -rf /").decision is PermissionDecision.DENY
    assert _decide(policy, "sudo apt-get install x").decision is PermissionDecision.ASK
    assert _decide(policy, "ls -la").decision is PermissionDecision.ALLOW


def test_strict_mode_denies_dangerous_asks_caution() -> None:
    policy = PermissionPolicy(mode=PermissionMode.STRICT)
    assert _decide(policy, "sudo apt-get install x").decision is PermissionDecision.DENY
    assert _decide(policy, "rm notes.txt").decision is PermissionDecision.ASK
    assert _decide(policy, "ls -la").decision is PermissionDecision.ALLOW


def test_yolo_mode_allows_everything() -> None:
    policy = PermissionPolicy(mode=PermissionMode.YOLO)
    assert _decide(policy, "rm -rf /").decision is PermissionDecision.ALLOW


def test_explicit_rule_wins_over_mode() -> None:
    policy = PermissionPolicy(
        mode=PermissionMode.YOLO,
        rules=(
            PermissionRule(
                decision=PermissionDecision.DENY,
                tools=("bash",),
                command_includes="rm -rf",
                reason="no recursive delete",
            ),
        ),
    )
    out = _decide(policy, "rm -rf /tmp/x")
    assert out.decision is PermissionDecision.DENY
    assert out.reason == "no recursive delete"
    # A non-matching command falls back to the YOLO default.
    assert _decide(policy, "echo hi").decision is PermissionDecision.ALLOW


def test_rule_tool_glob_and_regex() -> None:
    policy = PermissionPolicy(
        mode=PermissionMode.STANDARD,
        rules=(
            PermissionRule(
                decision=PermissionDecision.ASK,
                tools=("db_*",),
                command_regex=r"\bDROP\s+TABLE\b",
            ),
        ),
    )
    assert (
        policy.decide("db_admin", {"command": "DROP TABLE users"}).decision
        is PermissionDecision.ASK
    )
    # Different tool name → rule does not match.
    assert (
        policy.decide("web_search", {"command": "DROP TABLE users"}).decision
        is PermissionDecision.ALLOW
    )


def test_non_command_tool_allows_by_default() -> None:
    policy = PermissionPolicy(mode=PermissionMode.STRICT)
    assert (
        policy.decide("web_search", {"query": "x"}).decision is PermissionDecision.ALLOW
    )


def test_bad_regex_rejected_at_load() -> None:
    with pytest.raises(ValueError):
        PermissionRule(decision=PermissionDecision.DENY, command_regex="(")
