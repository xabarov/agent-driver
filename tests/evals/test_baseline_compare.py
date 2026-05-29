"""Baseline report comparison tests."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.evals import (
    CaseEvaluation,
    EvalReport,
    EvaluatorResult,
    compare_reports,
)


def _report(*, report_id: str, case_status: bool) -> EvalReport:
    case = CaseEvaluation(
        case_id="case_1",
        output=AgentRunOutput(
            run_id="run_1",
            attempt_id="attempt_1",
            status=RunStatus.COMPLETED,
            terminal_reason=TerminalReason.FINAL_ANSWER,
            events=[
                new_runtime_event(
                    event_type=RuntimeEventType.RUN_COMPLETED,
                    context={"run_id": "run_1", "attempt_id": "attempt_1", "seq": 1},
                )
            ],
        ),
        evaluations=[EvaluatorResult(evaluator="event_schema", passed=case_status)],
        passed=case_status,
    )
    return EvalReport(
        report_id=report_id,
        candidate_id=report_id,
        cases=[case],
        passed_cases=1 if case_status else 0,
        failed_cases=0 if case_status else 1,
    )


def test_compare_reports_detects_regression() -> None:
    """Comparison should detect pass-to-fail transitions."""
    baseline = _report(report_id="baseline", case_status=True)
    candidate = _report(report_id="candidate", case_status=False)
    result = compare_reports(baseline=baseline, candidate=candidate)
    assert result.regressions == ["case_1:pass_to_fail"]
    assert result.improvements == []


def test_compare_reports_detects_improvement() -> None:
    """Comparison should detect fail-to-pass transitions."""
    baseline = _report(report_id="baseline", case_status=False)
    candidate = _report(report_id="candidate", case_status=True)
    result = compare_reports(baseline=baseline, candidate=candidate)
    assert result.improvements == ["case_1:fail_to_pass"]
