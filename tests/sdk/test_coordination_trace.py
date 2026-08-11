"""Coordination observability — describe/digest helpers (coordination trace)."""

from __future__ import annotations

from agent_driver.contracts.enums import (
    ApprovalMode,
    RunStatus,
    SideEffectClass,
    SubagentJoinPolicy,
    ToolRisk,
    ToolTraceStatus,
)
from agent_driver.contracts.tools.results import ToolTrace
from agent_driver.contracts.usage import UsageSummary
from agent_driver.sdk import (
    SubagentDigest,
    describe,
    digest_subagent,
)
from agent_driver.sdk.coordinator import CoordinatorResult, PhaseResult
from agent_driver.sdk.deep_agent import DeepAgentPlan, DeepAgentResult
from agent_driver.sdk.group import SubagentGroupResult
from agent_driver.sdk.subagent import SubagentResult


def _trace(step: int, name: str, status: ToolTraceStatus = ToolTraceStatus.COMPLETED) -> ToolTrace:
    return ToolTrace(
        step=step,
        tool_name=name,
        status=status,
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.NONE,
        approval_mode=ApprovalMode.NEVER,
    )


def _result(
    agent_type: str,
    status: RunStatus,
    answer: str | None,
    *,
    tools: tuple[ToolTrace, ...] = (),
    terminal_reason: str | None = None,
    cost: float | None = None,
    tokens: int = 0,
) -> SubagentResult:
    return SubagentResult(
        child_run_id=f"c-{agent_type}",
        parent_run_id="p",
        agent_type=agent_type,
        status=status,
        terminal_reason=terminal_reason,
        answer=answer,
        structured_output=None,
        tool_trace=tools,
        usage=UsageSummary(total_tokens=tokens, cost_usd_estimate=cost) if (tokens or cost) else None,
        raw_output=None,
    )


def _group(*results: SubagentResult | None, satisfied: bool = True) -> SubagentGroupResult:
    return SubagentGroupResult(
        results=tuple(results),
        errors=tuple(None for _ in results),
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        satisfied=satisfied,
    )


def test_digest_captures_salient_facts() -> None:
    res = _result(
        "worker",
        RunStatus.COMPLETED,
        "the answer text",
        tools=(_trace(0, "read_page"), _trace(1, "pandas")),
        cost=0.0012,
        tokens=500,
    )
    d = digest_subagent(res)
    assert isinstance(d, SubagentDigest)
    assert d.agent_type == "worker" and d.completed and not d.empty_answer
    assert d.tool_calls == 2 and d.tools == ("read_page", "pandas")
    assert d.failed_tools == () and d.cost_usd == 0.0012 and d.total_tokens == 500


def test_digest_of_missing_result() -> None:
    d = digest_subagent(None)
    assert d.agent_type == "<missing>" and d.status == "MISSING" and not d.completed


def test_describe_subagent_flags_empty_and_failed_tool() -> None:
    res = _result(
        "executor",
        RunStatus.FAILED,
        "",  # empty answer
        tools=(_trace(0, "read_page"), _trace(1, "excel_set_cell", ToolTraceStatus.DENIED)),
        terminal_reason="deadline_exceeded",
    )
    line = describe(res)
    assert line.startswith("✗ executor")
    assert "failed(deadline_exceeded)" in line
    assert "⚠empty" in line  # empty-answer flag
    assert "⚠tool-excel_set_cell" in line  # denied tool flagged
    assert "read_page" in line


def test_describe_subagent_completed_has_check() -> None:
    res = _result("w", RunStatus.COMPLETED, "done", tools=(_trace(0, "t"),))
    assert describe(res).startswith("✓ w")


def test_render_tools_collapses_repeats() -> None:
    res = _result(
        "w",
        RunStatus.COMPLETED,
        "x",
        tools=tuple(_trace(i, "read_page") for i in range(6)),
    )
    # 6 identical reads collapse to "read_page ×6", not a 6-item list
    assert "read_page ×6" in describe(res)


def test_describe_group_lists_children_and_policy() -> None:
    group = _group(
        _result("a", RunStatus.COMPLETED, "ok", tools=(_trace(0, "t"),)),
        _result("b", RunStatus.FAILED, "", terminal_reason="budget_exceeded"),
        None,
    )
    out = describe(group)
    assert "wait_all" in out and "satisfied=True" in out
    assert "✓ a" in out and "✗ b" in out and "<missing>" in out
    assert "budget_exceeded" in out


def test_describe_coordinator_shows_phases() -> None:
    p1 = PhaseResult(
        name="analyze",
        group=_group(_result("explorer", RunStatus.COMPLETED, "notes", tools=(_trace(0, "read_page"),))),
        merged="merged analysis text",
    )
    p2 = PhaseResult(
        name="execute",
        group=_group(_result("executor", RunStatus.COMPLETED, "edited", tools=(_trace(0, "excel_set_cell"),))),
        merged="edited",
    )
    result = CoordinatorResult(phases=(p1, p2))
    out = describe(result)
    assert "coordinator: 2 phases" in out
    assert "phase 'analyze'" in out and "phase 'execute'" in out
    assert "explorer" in out and "executor" in out and "excel_set_cell" in out


def test_describe_deep_agent_shows_plan_and_workers() -> None:
    group = _group(
        _result("editor_00", RunStatus.COMPLETED, "did sheet A", tools=(_trace(0, "excel_set_range"),)),
    )
    result = DeepAgentResult(
        task="assess",
        plan=DeepAgentPlan(task="assess", subtasks=("do sheet A", "do sheet B")),
        group=group,
        artifacts=(),
        answer="final brief",
        satisfied=True,
    )
    out = describe(result)
    assert "deep_agent: 2 subtasks" in out
    assert "do sheet A" in out and "editor_00" in out
    assert "synthesized answer" in out and "final brief" in out


def test_describe_falls_back_to_repr_for_unknown() -> None:
    assert describe(42) == "42"
