"""D6: scope-aware HITL — path_under fires only when a path could be touched."""

from __future__ import annotations

from agent_driver.permissions import (
    PermissionDecision,
    PermissionMode,
    PermissionPolicy,
    PermissionRule,
)


def _ask_under(prefix: str) -> PermissionPolicy:
    return PermissionPolicy(
        mode=PermissionMode.YOLO,  # default allow → only the rule can ask
        rules=(
            PermissionRule(
                decision=PermissionDecision.ASK,
                path_under=prefix,
                reason="touches protected path",
            ),
        ),
    )


def test_exact_path_under_protected_asks() -> None:
    policy = _ask_under("/etc")
    out = policy.decide("read_file", {"path": "/etc/passwd"})
    assert out.decision is PermissionDecision.ASK


def test_unrelated_path_allows() -> None:
    policy = _ask_under("/etc")
    out = policy.decide("read_file", {"path": "/home/user/notes.txt"})
    assert out.decision is PermissionDecision.ALLOW


def test_glob_rooted_above_protected_asks() -> None:
    # A bulk op rooted at "/" could reach /etc → ask.
    policy = _ask_under("/etc")
    out = policy.decide("grep", {"path": "/**/*.conf"})
    assert out.decision is PermissionDecision.ASK


def test_glob_inside_protected_asks() -> None:
    policy = _ask_under("/etc")
    out = policy.decide("list_dir", {"base_dir": "/etc/*"})
    assert out.decision is PermissionDecision.ASK


def test_no_path_argument_does_not_match() -> None:
    policy = _ask_under("/etc")
    # No path-bearing arg → the scope rule cannot match → default allow.
    out = policy.decide("web_search", {"query": "hello"})
    assert out.decision is PermissionDecision.ALLOW


def test_sibling_prefix_not_confused() -> None:
    # "/etchosts" must not be treated as under "/etc".
    policy = _ask_under("/etc")
    out = policy.decide("read_file", {"path": "/etchosts/file"})
    assert out.decision is PermissionDecision.ALLOW


def test_path_under_combines_with_tool_glob() -> None:
    policy = PermissionPolicy(
        mode=PermissionMode.YOLO,
        rules=(
            PermissionRule(
                decision=PermissionDecision.DENY,
                tools=("write_file", "delete_*"),
                path_under="/etc",
            ),
        ),
    )
    assert (
        policy.decide("write_file", {"path": "/etc/x"}).decision
        is PermissionDecision.DENY
    )
    # Right path, wrong tool → no match → allow.
    assert (
        policy.decide("read_file", {"path": "/etc/x"}).decision
        is PermissionDecision.ALLOW
    )
