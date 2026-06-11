"""Tests for ``agent_driver.runtime.planning_check``."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.contracts.runtime import AgentRunOutput, RuntimeEvent
from agent_driver.contracts.tools.results import ToolTrace
from agent_driver.runtime.planning_check import (
    CANONICAL_EXIT_PLAN_MODE_TOOL,
    EXIT_PLAN_MODE_TOOL_NAMES,
    LEGACY_EXIT_PLAN_MODE_TOOL_ALIASES,
    PLANNING_TOOL_NAMES,
    data_tool_called,
    is_exit_plan_mode_tool,
    planning_executed,
    planning_executed_across,
    planning_tool_called,
)


def _trace(step: int, tool_name: str) -> ToolTrace:
    return ToolTrace(
        step=step,
        tool_name=tool_name,
        status="completed",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.NONE,
        approval_mode=ApprovalMode.NEVER,
    )


def _output(*tool_names: str) -> AgentRunOutput:
    """Build a minimal terminal ``AgentRunOutput`` for the given tool trace.

    The contract requires a terminal runtime event on completed outputs,
    so we synthesize a single ``run_completed`` event matching the run id.
    """
    return AgentRunOutput(
        run_id="run_test",
        attempt_id="attempt_1",
        status="completed",
        terminal_reason="final_answer",
        answer="ok",
        events=[
            RuntimeEvent(
                event_id="evt_term",
                type="run_completed",
                run_id="run_test",
                attempt_id="attempt_1",
                seq=1,
                created_at=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
                payload={},
            ),
        ],
        tool_trace=[_trace(i + 1, name) for i, name in enumerate(tool_names)],
    )


# Constant exposure


def test_planning_tool_names_includes_canonical_tools() -> None:
    """Default set covers the tools shipped by ``tools.planning``."""
    assert "todo_write" in PLANNING_TOOL_NAMES
    assert "planning_state_update" in PLANNING_TOOL_NAMES
    assert "enter_plan_mode" in PLANNING_TOOL_NAMES
    assert "exit_plan_mode_v2" in PLANNING_TOOL_NAMES
    assert "exit_plan_mode" in PLANNING_TOOL_NAMES
    assert CANONICAL_EXIT_PLAN_MODE_TOOL == "exit_plan_mode_v2"
    assert EXIT_PLAN_MODE_TOOL_NAMES == frozenset(
        {"exit_plan_mode_v2", "exit_plan_mode"}
    )
    assert LEGACY_EXIT_PLAN_MODE_TOOL_ALIASES == frozenset({"exit_plan_mode"})
    assert is_exit_plan_mode_tool("exit_plan_mode_v2") is True
    assert is_exit_plan_mode_tool("exit_plan_mode") is True
    # ask_user_question is deliberately NOT a planning tool — it's HITL.
    assert "ask_user_question" not in PLANNING_TOOL_NAMES


# planning_tool_called / data_tool_called


def test_planning_tool_called_true_for_todo_write_only_run() -> None:
    assert planning_tool_called(_output("todo_write")) is True


def test_planning_tool_called_false_when_no_planning_tool() -> None:
    assert planning_tool_called(_output("file_read", "file_write")) is False


def test_data_tool_called_true_when_non_planning_tool_present() -> None:
    assert data_tool_called(_output("todo_write", "file_read")) is True


def test_data_tool_called_false_when_only_planning_tools() -> None:
    assert (
        data_tool_called(_output("todo_write", "planning_state_update")) is False
    )


def test_data_tool_called_false_on_empty_trace() -> None:
    assert data_tool_called(_output()) is False


# planning_executed tri-state


def test_planning_executed_none_when_planning_not_engaged() -> None:
    """No planning tool → returns None (not False) so the caller can tell
    "we weren't in plan mode" apart from "plan mode but fabricated"."""
    assert planning_executed(_output("file_read")) is None
    assert planning_executed(_output()) is None


def test_planning_executed_true_when_planning_and_data_both_present() -> None:
    assert planning_executed(_output("todo_write", "file_read")) is True


def test_planning_executed_false_when_planning_without_data() -> None:
    """The fabrication-detection case — model planned but never executed."""
    assert planning_executed(_output("todo_write")) is False
    assert (
        planning_executed(_output("todo_write", "planning_state_update")) is False
    )


def test_planning_executed_respects_custom_planning_set() -> None:
    """Caller can extend the planning set with their own planners.

    Sanity check: the same trace should classify differently when
    "my_house_planner" is registered as a planning tool vs not.
    """
    extended = PLANNING_TOOL_NAMES | {"my_house_planner"}
    # Trace with only "my_house_planner" — no planning tool from the
    # default set, no other data tool.
    out_only_custom = _output("my_house_planner")
    # Default set sees no planning tool → None.
    assert planning_executed(out_only_custom) is None
    # Extended set treats my_house_planner as planning, no data tool → False.
    assert (
        planning_executed(out_only_custom, planning_tool_names=extended)
        is False
    )
    # If we add a real data tool, the extended-set verdict flips to True.
    out_custom_and_data = _output("my_house_planner", "file_read")
    assert (
        planning_executed(out_custom_and_data, planning_tool_names=extended)
        is True
    )


# planning_executed_across


def test_planning_executed_across_combines_traces_from_multiple_runs() -> None:
    """First run only planned; second run executed → combined verdict True."""
    first = _output("todo_write")
    second = _output("file_read")
    assert planning_executed_across([first, second]) is True


def test_planning_executed_across_returns_false_when_no_run_executed() -> None:
    """Both runs planned, neither executed — typical D-004-mitigation
    case where the retry also failed."""
    first = _output("todo_write")
    second = _output("todo_write")
    assert planning_executed_across([first, second]) is False


def test_planning_executed_across_returns_none_when_no_planning_anywhere() -> None:
    first = _output("file_read")
    second = _output("file_write")
    assert planning_executed_across([first, second]) is None


def test_planning_executed_across_short_circuits_on_first_success() -> None:
    """When a single output already contains both planning + data, the
    verdict is True and remaining outputs needn't be scanned."""
    first = _output("todo_write", "file_read")
    second = _output("more_calls")
    # We can't directly observe short-circuit, but the result must match
    # the documented short-circuit behaviour.
    assert planning_executed_across([first, second]) is True


def test_planning_executed_across_handles_empty_iterable() -> None:
    assert planning_executed_across([]) is None
