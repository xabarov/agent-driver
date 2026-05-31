"""Post-run analysis: did the agent actually execute its plan?

When an agent runs in plan mode (driven by ``todo_write`` /
``planning_state_update`` / ``enter_plan_mode``), a common failure mode is
to emit a plan and then a prose answer without invoking any data /
analysis tool — the answer is almost certainly fabricated.

This module exposes pure primitives to detect that condition.

  * Detection only — no retry policy. The caller decides whether to retry,
    surface a warning, or both. Retry usually requires re-invoking the
    agent with a caller-specific directive (referencing concrete tool
    names), so the library can't author it generically.
  * Operates on already-finished ``AgentRunOutput`` objects via
    ``tool_trace``. Works for both single runs and combined (multi-run)
    traces.
  * The default planning tool set covers the tools shipped under
    ``agent_driver.tools.planning``; callers using extra in-house
    planners can pass their own set.

Discovered as a recurring need by ``excel_ai`` (D-004 fabrication
mitigation) — see ``docs/qa-evaluation-roadmap.md`` in that project for
the original triage. Lifted out of project code because the same shape
applies to any consumer that uses ``todo_write``.
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_driver.contracts.runtime import AgentRunOutput


CANONICAL_EXIT_PLAN_MODE_TOOL = "exit_plan_mode_v2"
LEGACY_EXIT_PLAN_MODE_TOOL_ALIASES: frozenset[str] = frozenset({"exit_plan_mode"})
EXIT_PLAN_MODE_TOOL_NAMES: frozenset[str] = frozenset(
    {CANONICAL_EXIT_PLAN_MODE_TOOL, *LEGACY_EXIT_PLAN_MODE_TOOL_ALIASES}
)

# Built-in planning tools registered by
# ``agent_driver.tools.planning.register_planning_tool``. We intentionally
# leave ``ask_user_question`` out — it's a HITL clarification pause, not
# a planning step in the "plan vs. execution" sense the validator cares
# about. ``exit_plan_mode_v2`` is the canonical registered approval-exit
# tool; ``exit_plan_mode`` is kept here only as a trace/history alias.
# Callers can extend this set via the ``planning_tool_names`` argument.
PLANNING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "todo_write",
        "planning_state_update",
        "enter_plan_mode",
        *EXIT_PLAN_MODE_TOOL_NAMES,
    }
)


def is_exit_plan_mode_tool(name: str) -> bool:
    """Return True for canonical approval-exit tool or legacy trace aliases."""
    return name in EXIT_PLAN_MODE_TOOL_NAMES


def _tool_names_in(output: AgentRunOutput) -> list[str]:
    """Extract tool names from an ``AgentRunOutput.tool_trace``."""
    return [trace.tool_name for trace in (output.tool_trace or [])]


def planning_tool_called(
    output: AgentRunOutput,
    *,
    planning_tool_names: Iterable[str] = PLANNING_TOOL_NAMES,
) -> bool:
    """Return True if the run invoked at least one planning tool."""
    planning = set(planning_tool_names)
    return any(name in planning for name in _tool_names_in(output))


def data_tool_called(
    output: AgentRunOutput,
    *,
    planning_tool_names: Iterable[str] = PLANNING_TOOL_NAMES,
) -> bool:
    """Return True if the run invoked at least one non-planning tool.

    "Data tool" here is shorthand for "anything outside
    ``planning_tool_names``" — i.e., any tool that actually touches the
    domain (read a file, query a DB, run code, render a chart).
    """
    planning = set(planning_tool_names)
    return any(name not in planning for name in _tool_names_in(output))


def planning_executed(
    output: AgentRunOutput,
    *,
    planning_tool_names: Iterable[str] = PLANNING_TOOL_NAMES,
) -> bool | None:
    """Classify a single run's planning outcome.

    Returns:
      ``None``   — planning not engaged this run (no planning tool called).
                   Caller is presumably not in plan mode; nothing to assess.
      ``True``   — planning engaged AND a data tool ran; the plan was
                   actually executed.
      ``False``  — planning engaged but no data tool ran. The final answer
                   is almost certainly fabricated (the model produced a
                   plan and then emitted prose without doing any of it).
                   Callers typically warn the user and/or retry once with
                   an explicit directive.
    """
    planning = set(planning_tool_names)
    saw_planning = False
    saw_data = False
    for name in _tool_names_in(output):
        if name in planning:
            saw_planning = True
        else:
            saw_data = True
        if saw_planning and saw_data:
            break
    if not saw_planning:
        return None
    return saw_data


def planning_executed_across(
    outputs: Iterable[AgentRunOutput],
    *,
    planning_tool_names: Iterable[str] = PLANNING_TOOL_NAMES,
) -> bool | None:
    """``planning_executed`` over a sequence of ``AgentRunOutput`` objects.

    Useful when the caller orchestrated an auto-retry (e.g. one re-run
    after a fabricated first attempt) and wants a single verdict
    summarising both attempts. The tool trace is unioned across all
    outputs.
    """
    planning = set(planning_tool_names)
    saw_planning = False
    saw_data = False
    for output in outputs:
        for name in _tool_names_in(output):
            if name in planning:
                saw_planning = True
            else:
                saw_data = True
            if saw_planning and saw_data:
                return True
    if not saw_planning:
        return None
    return saw_data


__all__ = [
    "CANONICAL_EXIT_PLAN_MODE_TOOL",
    "EXIT_PLAN_MODE_TOOL_NAMES",
    "LEGACY_EXIT_PLAN_MODE_TOOL_ALIASES",
    "PLANNING_TOOL_NAMES",
    "data_tool_called",
    "is_exit_plan_mode_tool",
    "planning_executed",
    "planning_executed_across",
    "planning_tool_called",
]
