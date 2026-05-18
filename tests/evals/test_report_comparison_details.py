"""Detailed report comparison tests."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    ToolRisk,
    ToolTrace,
    ToolTraceStatus,
    UsageSummary,
    new_runtime_event,
)
from agent_driver.evals import (
    CaseEvaluation,
    EvalReport,
    EvaluatorResult,
    compare_reports,
)


def _case(*, case_id: str, status: str, seq_start: int, tokens: int) -> CaseEvaluation:
    output = AgentRunOutput(
        run_id=f"run_{case_id}",
        attempt_id="attempt_1",
        status=status,
        terminal_reason=(
            TerminalReason.FINAL_ANSWER
            if status == RunStatus.COMPLETED
            else TerminalReason.RUNTIME_ERROR
        ),
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={
                    "run_id": f"run_{case_id}",
                    "attempt_id": "attempt_1",
                    "seq": seq_start,
                },
            ),
            new_runtime_event(
                event_type=(
                    RuntimeEventType.RUN_COMPLETED
                    if status == RunStatus.COMPLETED
                    else RuntimeEventType.RUN_FAILED
                ),
                context={
                    "run_id": f"run_{case_id}",
                    "attempt_id": "attempt_1",
                    "seq": seq_start + 1,
                },
            ),
        ],
        tool_trace=[
            ToolTrace(
                step=0,
                tool_name="lookup",
                status=ToolTraceStatus.COMPLETED,
                risk=ToolRisk.LOW,
                side_effect="read_only",
                approval_mode="never",
            )
        ],
        usage=UsageSummary(total_tokens=tokens),
    )
    return CaseEvaluation(
        case_id=case_id,
        output=output,
        evaluations=[EvaluatorResult(evaluator="event_schema", passed=True)],
        passed=status == RunStatus.COMPLETED,
    )


def test_compare_reports_tracks_terminal_and_budget_deltas() -> None:
    """Comparison should include trajectory and budget deltas in details."""
    baseline = EvalReport(
        report_id="r_base",
        candidate_id="base",
        cases=[
            _case(case_id="case_1", status=RunStatus.COMPLETED, seq_start=1, tokens=10)
        ],
        passed_cases=1,
        failed_cases=0,
    )
    candidate = EvalReport(
        report_id="r_cand",
        candidate_id="cand",
        cases=[
            _case(case_id="case_1", status=RunStatus.FAILED, seq_start=1, tokens=25)
        ],
        passed_cases=0,
        failed_cases=1,
    )
    result = compare_reports(baseline=baseline, candidate=candidate)
    assert "case_1:terminal_state_changed" in result.regressions
    case_details = result.details["cases"]["case_1"]
    assert case_details["budget_delta"]["tokens_delta"] == 15
