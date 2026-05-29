"""Local deterministic dataset runner for Phase-5 harness."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha1

from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.evals.contracts import (
    BudgetLimits,
    CaseEvaluation,
    DatasetCase,
    EvalReport,
)
from agent_driver.evals.evaluators import default_evaluators

RunExecutor = Callable[[DatasetCase], Awaitable[AgentRunOutput]]


def _report_id(candidate_id: str, case_ids: list[str]) -> str:
    seed = f"{candidate_id}:{','.join(case_ids)}"
    return f"eval_{sha1(seed.encode('utf-8')).hexdigest()[:16]}"


async def run_dataset(
    *,
    cases: list[DatasetCase],
    run_executor: RunExecutor,
    candidate_id: str,
    limits: BudgetLimits | None = None,
) -> EvalReport:
    """Execute dataset cases and evaluate each output deterministically."""
    evaluators = default_evaluators(limits=limits)
    case_evaluations: list[CaseEvaluation] = []
    for case in cases:
        output = await run_executor(case)
        results = [evaluator(output) for evaluator in evaluators]
        case_checks: list[bool] = [item.passed for item in results]
        if case.expected_status is not None:
            case_checks.append(output.status == case.expected_status)
        if case.expected_terminal_reason is not None:
            case_checks.append(output.terminal_reason == case.expected_terminal_reason)
        case_evaluations.append(
            CaseEvaluation(
                case_id=case.case_id,
                output=output,
                evaluations=results,
                passed=all(case_checks),
                metadata={
                    "description": case.description,
                    "expected_status": (
                        case.expected_status.value if case.expected_status else None
                    ),
                    "expected_terminal_reason": (
                        case.expected_terminal_reason.value
                        if case.expected_terminal_reason
                        else None
                    ),
                },
            )
        )

    passed_cases = sum(1 for item in case_evaluations if item.passed)
    failed_cases = len(case_evaluations) - passed_cases
    return EvalReport(
        report_id=_report_id(candidate_id, [case.case_id for case in cases]),
        candidate_id=candidate_id,
        cases=case_evaluations,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        metadata={"case_count": len(case_evaluations)},
    )
