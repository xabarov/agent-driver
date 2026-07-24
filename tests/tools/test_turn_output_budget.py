"""Epic 033 B tier 3: per-turn aggregate tool-output budget."""

from __future__ import annotations

from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.tools.executor.turn_budget import enforce_turn_output_budget


def _env(name: str, summary: str) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        call=ToolCall(tool_name=name, tool_call_id=f"c-{name}", args={}),
        summary=summary,
    )


def test_off_when_budget_none() -> None:
    envs = [_env("a", "x" * 5000)]
    out, audit = enforce_turn_output_budget(envs, budget_chars=None)
    assert out is envs and audit["activated"] is False


def test_under_budget_is_noop() -> None:
    envs = [_env("a", "x" * 100), _env("b", "y" * 100)]
    out, audit = enforce_turn_output_budget(envs, budget_chars=10_000)
    assert audit["activated"] is False
    assert [e.summary for e in out] == [e.summary for e in envs]


def test_trims_largest_until_under_budget() -> None:
    envs = [
        _env("small", "s" * 500),
        _env("big", "B" * 8000),
        _env("medium", "m" * 3000),
    ]
    out, audit = enforce_turn_output_budget(envs, budget_chars=4000, preview_chars=1000)
    assert audit["activated"] is True
    assert audit["spilled_count"] >= 1
    assert audit["chars_saved"] > 0
    # The biggest summary is now trimmed + marked; the small one is untouched.
    big = next(e for e in out if e.call.tool_name == "big")
    assert big.truncated is True
    assert big.metadata.get("turn_budget_truncated") is True
    assert len(big.summary) < 8000
    small = next(e for e in out if e.call.tool_name == "small")
    assert small.summary == "s" * 500
    # Aggregate now under budget.
    total = sum(len(e.summary or "") for e in out)
    assert total <= 4000


def test_already_marked_not_retrimmed() -> None:
    env = _env("big", "B" * 6000).model_copy(
        update={"metadata": {"turn_budget_truncated": True}}
    )
    out, audit = enforce_turn_output_budget([env], budget_chars=1000)
    # Nothing more can be trimmed → no spill, summary unchanged.
    assert audit["spilled_count"] == 0
    assert out[0].summary == "B" * 6000
