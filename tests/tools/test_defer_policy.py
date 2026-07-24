"""Epic 033 A: adaptive tool-deferral threshold (hermes should_activate)."""

from __future__ import annotations

from agent_driver.contracts import ToolManifest
from agent_driver.runtime.single_agent.llm_step.build import adaptive_defer_surface
from agent_driver.tools import ToolRegistry
from agent_driver.tools.defer_policy import (
    estimate_schema_tokens,
    should_activate_deferral,
)


async def _noop(_args):
    return {}


def test_should_activate_modes() -> None:
    assert should_activate_deferral(50_000, 200_000, mode="off") is False
    assert should_activate_deferral(1, 200_000, mode="on") is True
    assert should_activate_deferral(0, 200_000, mode="on") is False  # nothing to defer
    # auto: 10% of 200k = 20k threshold
    assert should_activate_deferral(19_000, 200_000, mode="auto") is False
    assert should_activate_deferral(21_000, 200_000, mode="auto") is True
    # auto, unknown window → 20k fixed cliff
    assert should_activate_deferral(19_000, None, mode="auto") is False
    assert should_activate_deferral(20_000, None, mode="auto") is True


def test_estimate_schema_tokens_monotonic() -> None:
    small = [{"function": {"name": "a", "parameters": {}}}]
    big = [{"function": {"name": "a", "parameters": {"x": "y" * 4000}}}]
    assert estimate_schema_tokens(big) > estimate_schema_tokens(small) > 0


def _registry_with_deferred(n_deferred: int, desc_len: int) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolManifest(
            name="core_read",
            description="read",
            args_schema={"type": "object", "properties": {}},
        ),
        _noop,
    )
    for i in range(n_deferred):
        reg.register(
            ToolManifest(
                name=f"deferred_{i}",
                description="D" * desc_len,
                args_schema={"type": "object", "properties": {}},
                should_defer=True,
            ),
            _noop,
        )
    return reg


def test_below_threshold_force_surfaces_candidates() -> None:
    # Two tiny deferred tools, huge window → far below 10% → surface both.
    reg = _registry_with_deferred(2, desc_len=20)
    names, audit = adaptive_defer_surface(
        reg,
        allowed=None,
        denied=None,
        already_surfaced=(),
        context_window=200_000,
        mode="auto",
        threshold_pct=10.0,
    )
    assert set(names) == {"deferred_0", "deferred_1"}
    assert audit["activated"] is False
    assert audit["candidate_count"] == 2


def test_above_threshold_defers_candidates() -> None:
    # Many bulky deferred tools → cross 10% of a small window → defer (surface none).
    reg = _registry_with_deferred(40, desc_len=4000)
    names, audit = adaptive_defer_surface(
        reg,
        allowed=None,
        denied=None,
        already_surfaced=(),
        context_window=32_000,
        mode="auto",
        threshold_pct=10.0,
    )
    assert names == ()
    assert audit["activated"] is True
    assert audit["deferred_tokens_saved"] > 0


def test_mode_on_always_defers() -> None:
    reg = _registry_with_deferred(1, desc_len=10)
    names, audit = adaptive_defer_surface(
        reg,
        allowed=None,
        denied=None,
        already_surfaced=(),
        context_window=200_000,
        mode="on",
        threshold_pct=10.0,
    )
    assert names == ()
    assert audit["activated"] is True


def test_no_deferred_candidates_is_inert() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolManifest(
            name="a", description="x", args_schema={"type": "object", "properties": {}}
        ),
        _noop,
    )
    names, audit = adaptive_defer_surface(
        reg,
        allowed=None,
        denied=None,
        already_surfaced=(),
        context_window=200_000,
        mode="auto",
        threshold_pct=10.0,
    )
    assert names == ()
    assert audit["candidate_count"] == 0
