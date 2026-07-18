"""Force-final budget resolution: no hidden 1-tool-call fallback (observations 2026-07-18)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.tool_stage import (
    _force_final_reason,
    _resolved_budget,
)
from agent_driver.runtime.single_agent.types import (
    DEFAULT_MAX_STEPS_BACKSTOP,
    DEFAULT_MAX_TOOL_CALLS_BACKSTOP,
)


def _context(*, tool_calls: int, llm_steps: int = 1, metadata: dict | None = None):
    return SimpleNamespace(
        run_input=SimpleNamespace(
            max_tool_calls=None,
            max_steps=None,
            tool_policy=SimpleNamespace(metadata={}),
            app_metadata={},
            input="q",
        ),
        metadata=dict(metadata or {}),
        tool_calls=tool_calls,
        llm_step_count=llm_steps,
    )


def test_resolved_budget_precedence():
    assert _resolved_budget(3, 10, 80) == 3  # per-run wins
    assert _resolved_budget(None, 10, 80) == 10  # stamped runner default
    assert _resolved_budget(None, None, 80) == 80  # backstop, NOT 1
    assert _resolved_budget(0, None, 80) == 1  # floor


def test_no_forced_final_after_first_tool_call_without_budgets():
    """Хост без явных бюджетов больше не получает форс-финал после ПЕРВОГО tool-вызова."""
    context = _context(tool_calls=1)
    assert _force_final_reason(context) is None


def test_forced_final_near_backstop_budgets():
    context = _context(tool_calls=DEFAULT_MAX_TOOL_CALLS_BACKSTOP - 1)
    assert _force_final_reason(context) == "near_tool_budget"
    context = _context(tool_calls=0, llm_steps=DEFAULT_MAX_STEPS_BACKSTOP - 1)
    assert _force_final_reason(context) == "near_step_budget"


def test_forced_final_honors_stamped_runner_defaults():
    context = _context(tool_calls=5, metadata={"max_tool_calls": 6, "max_steps": 12})
    assert _force_final_reason(context) == "near_tool_budget"
    context = _context(tool_calls=2, metadata={"max_tool_calls": 6, "max_steps": 12})
    assert _force_final_reason(context) is None


def test_refunded_housekeeping_calls_do_not_burn_tool_budget():
    """Epic 019 phase D (hermes refund reference): planning/todo bookkeeping calls are
    refunded, so a plan-disciplined agent keeps the same effective search budget."""
    context = _context(tool_calls=6, metadata={"max_tool_calls": 6, "max_steps": 12})
    assert _force_final_reason(context) == "near_tool_budget"
    context = _context(
        tool_calls=6,
        metadata={"max_tool_calls": 6, "max_steps": 12, "refunded_tool_calls": 3},
    )
    assert _force_final_reason(context) is None  # effective 3 of 6
