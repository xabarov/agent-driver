"""Baseline/candidate report comparison utilities."""

from __future__ import annotations

from agent_driver.evals.contracts import EvalReport, ReportComparison, _status_bucket


def _case_map(report: EvalReport) -> dict[str, bool]:
    return {item.case_id: item.passed for item in report.cases}


def _case_by_id(report: EvalReport, case_id: str):
    for item in report.cases:
        if item.case_id == case_id:
            return item
    raise ValueError(f"Case '{case_id}' not found")


def compare_reports(  # pylint: disable=too-many-locals
    *, baseline: EvalReport, candidate: EvalReport
) -> ReportComparison:
    """Compare candidate report against baseline by outcomes and trajectories."""
    baseline_cases = _case_map(baseline)
    candidate_cases = _case_map(candidate)
    regressions: list[str] = []
    improvements: list[str] = []
    detail_rows: dict[str, dict[str, object]] = {}
    for case_id, baseline_passed in baseline_cases.items():
        candidate_passed = candidate_cases.get(case_id)
        if candidate_passed is None:
            regressions.append(f"{case_id}:missing_in_candidate")
            continue
        if baseline_passed and not candidate_passed:
            regressions.append(f"{case_id}:pass_to_fail")
        if not baseline_passed and candidate_passed:
            improvements.append(f"{case_id}:fail_to_pass")
        baseline_case = _case_by_id(baseline, case_id)
        candidate_case = _case_by_id(candidate, case_id)
        baseline_bucket = _status_bucket(baseline_case)
        candidate_bucket = _status_bucket(candidate_case)
        trajectory_changed = (
            baseline_bucket["trajectory"] != candidate_bucket["trajectory"]
        )
        terminal_changed = (
            baseline_bucket["status"] != candidate_bucket["status"]
            or baseline_bucket["terminal_reason"] != candidate_bucket["terminal_reason"]
        )
        tool_status_changed = (
            baseline_bucket["tool_statuses"] != candidate_bucket["tool_statuses"]
        )
        budget_delta = {
            "tokens_delta": int(candidate_bucket["total_tokens"])
            - int(baseline_bucket["total_tokens"]),
            "cost_delta": (
                (candidate_bucket["cost_usd_estimate"] or 0.0)
                - (baseline_bucket["cost_usd_estimate"] or 0.0)
            ),
        }
        if terminal_changed:
            regressions.append(f"{case_id}:terminal_state_changed")
        if trajectory_changed:
            regressions.append(f"{case_id}:trajectory_changed")
        if tool_status_changed:
            regressions.append(f"{case_id}:tool_statuses_changed")
        detail_rows[case_id] = {
            "baseline": baseline_bucket,
            "candidate": candidate_bucket,
            "budget_delta": budget_delta,
        }

    return ReportComparison(
        baseline_report_id=baseline.report_id,
        candidate_report_id=candidate.report_id,
        regressions=sorted(set(regressions)),
        improvements=sorted(improvements),
        details={
            "baseline_passed": baseline.passed_cases,
            "baseline_failed": baseline.failed_cases,
            "candidate_passed": candidate.passed_cases,
            "candidate_failed": candidate.failed_cases,
            "cases": detail_rows,
        },
    )
