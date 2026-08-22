"""opencode-adoption EPIC-09 — progressive tool-catalog disclosure (round-robin).

When deferral activates (candidate schemas cross the window fraction), a positive
``disclosure_budget_tokens`` inlines a namespace-fair, token-budgeted slice of the
deferred tools instead of surfacing nothing; the tail stays discoverable via
``tool_search``. Budget 0 keeps the historical all-or-nothing behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import ToolManifest
from agent_driver.runtime.single_agent.llm_step.build import (
    _round_robin_disclosure,
    _tool_namespace,
    adaptive_defer_surface,
)
from agent_driver.tools import ToolRegistry


async def _noop(_args):
    return {}


def test_tool_namespace() -> None:
    assert _tool_namespace("mcp__everything__get_sum") == "mcp__everything"
    assert _tool_namespace("a__b") == "a"
    assert _tool_namespace("read_file") == ""


def test_round_robin_is_namespace_fair_and_budget_bounded() -> None:
    # 2 namespaces, 3 tools each, every tool costs 10 tokens; budget fits 4 tools.
    cands = [
        SimpleNamespace(name=f"mcp__A__t{i}") for i in range(3)
    ] + [SimpleNamespace(name=f"mcp__B__t{i}") for i in range(3)]
    per = {c.name: 10 for c in cands}
    surfaced, used = _round_robin_disclosure(cands, per, budget_tokens=45)
    assert used == 40  # 4 tools * 10, the 5th (50) would overflow 45
    # fairness: both namespaces represented before either gets a third pick
    assert surfaced[:2] == ["mcp__A__t0", "mcp__B__t0"]
    assert {n.split("__")[1] for n in surfaced} == {"A", "B"}


def test_round_robin_zero_budget_surfaces_nothing() -> None:
    cands = [SimpleNamespace(name="mcp__A__t0")]
    surfaced, used = _round_robin_disclosure(cands, {"mcp__A__t0": 5}, budget_tokens=0)
    assert surfaced == []
    assert used == 0


def _registry_multi_namespace(namespaces: int, per_ns: int, desc_len: int) -> ToolRegistry:
    reg = ToolRegistry()
    for s in range(namespaces):
        for i in range(per_ns):
            reg.register(
                ToolManifest(
                    name=f"mcp__srv{s}__tool{i}",
                    description="D" * desc_len,
                    args_schema={"type": "object", "properties": {}},
                    should_defer=True,
                ),
                _noop,
            )
    return reg


def _surface(reg, *, budget: int):
    return adaptive_defer_surface(
        reg,
        allowed=None,
        denied=None,
        already_surfaced=(),
        context_window=32_000,
        mode="auto",
        threshold_pct=10.0,
        disclosure_budget_tokens=budget,
    )


def test_activated_deferral_surfaces_nothing_when_budget_zero() -> None:
    # Bulky deferred tools cross the threshold → deferral activates.
    reg = _registry_multi_namespace(namespaces=3, per_ns=6, desc_len=4000)
    names, audit = _surface(reg, budget=0)
    assert names == ()
    assert audit["activated"] is True
    assert audit["deferred_tokens_saved"] > 0
    assert "disclosure_mode" not in audit


def test_activated_deferral_inlines_fair_slice_with_budget() -> None:
    reg = _registry_multi_namespace(namespaces=3, per_ns=6, desc_len=4000)
    # Per-tool schema ≈ 1033 tokens; a ~3500 budget fits exactly one tool per namespace.
    names, audit = _surface(reg, budget=3500)
    assert audit["activated"] is True
    assert audit["disclosure_mode"] == "round_robin"
    assert 0 < audit["surfaced_count"] < 18
    assert audit["deferred_count"] == 18 - audit["surfaced_count"]
    assert audit["disclosure_tokens_used"] <= 3500
    # fairness: the budgeted slice draws from all three namespaces (round-robin),
    # not six tools from one server.
    surfaced_ns = {_tool_namespace(n) for n in names}
    assert surfaced_ns == {"mcp__srv0", "mcp__srv1", "mcp__srv2"}
    # and the tail (the other 15) defers — still reachable via tool_search.
    assert audit["deferred_count"] >= 12
