"""Configurable doom-loop repeat guard (opencode-adoption EPIC-02).

The runtime already forced a final answer when the last two tool calls were identical
(``_has_repeated_recent_tool_call``, default-on). This generalizes that hardcoded 2 into
``RunnerConfig.repeat_call_guard_threshold`` (last-N consecutive identical calls; default
2 = historical behaviour; 0/1 disables) so a host can tune it — raise it for agents that
legitimately repeat, lower it, or turn it off.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.tool_stage.guards import (
    _has_repeated_recent_tool_call,
    _repeat_call_guard_threshold,
)
from agent_driver.runtime.single_agent.types import RunnerConfig


def _ctx(calls: list[tuple[str, dict]], *, threshold: int | None = None):
    md: dict = {
        "tool_results": [{"call": {"tool_name": t, "args": a}} for t, a in calls]
    }
    if threshold is not None:
        md["repeat_call_guard_threshold"] = threshold
    return SimpleNamespace(metadata=md)


X = ("read_file", {"path": "x"})
Y = ("read_file", {"path": "y"})
Z = ("write_file", {"path": "x"})  # same args as X, different tool → distinct signature


def test_config_default_is_two() -> None:
    assert RunnerConfig().repeat_call_guard_threshold == 2
    # No metadata key → the historical default of 2.
    assert _repeat_call_guard_threshold(SimpleNamespace(metadata={})) == 2


def test_default_fires_on_two_consecutive_identical() -> None:
    assert _has_repeated_recent_tool_call(_ctx([X, X]))


def test_no_fire_on_single_or_distinct_tail() -> None:
    assert not _has_repeated_recent_tool_call(_ctx([X]))
    assert not _has_repeated_recent_tool_call(_ctx([X, Y]))
    assert not _has_repeated_recent_tool_call(_ctx([X, Z]))  # differs by tool name


def test_only_the_consecutive_tail_counts() -> None:
    # Two identical then a different call at the tail is not a loop at the tail.
    assert not _has_repeated_recent_tool_call(_ctx([X, X, Y]))
    # A different call then two identical at the tail IS a loop (threshold 2).
    assert _has_repeated_recent_tool_call(_ctx([Y, X, X]))


def test_threshold_three_needs_three_consecutive() -> None:
    assert not _has_repeated_recent_tool_call(_ctx([X, X], threshold=3))
    assert _has_repeated_recent_tool_call(_ctx([X, X, X], threshold=3))
    # An interruption resets the run: X,X,Y,X,X has only 2 identical at the tail.
    assert not _has_repeated_recent_tool_call(_ctx([X, X, Y, X, X], threshold=3))


def test_threshold_zero_or_one_disables() -> None:
    many = [X, X, X, X, X]
    assert not _has_repeated_recent_tool_call(_ctx(many, threshold=0))
    assert not _has_repeated_recent_tool_call(_ctx(many, threshold=1))


def test_non_int_threshold_falls_back_to_two() -> None:
    assert _repeat_call_guard_threshold(
        SimpleNamespace(metadata={"repeat_call_guard_threshold": "bogus"})
    ) == 2
