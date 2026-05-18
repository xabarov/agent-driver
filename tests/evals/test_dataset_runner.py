"""Dataset runner tests for deterministic local eval harness."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.evals import DatasetCase, run_dataset


async def _fake_executor(case: DatasetCase) -> AgentRunOutput:
    """Return deterministic output from case expectations."""
    status = case.expected_status or RunStatus.COMPLETED
    terminal_reason = case.expected_terminal_reason or (
        TerminalReason.FINAL_ANSWER
        if status == RunStatus.COMPLETED
        else TerminalReason.RUNTIME_ERROR
    )
    event_type = (
        RuntimeEventType.RUN_COMPLETED
        if status == RunStatus.COMPLETED
        else RuntimeEventType.RUN_FAILED
    )
    events = [
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={
                "run_id": case.run_input.run_id or case.case_id,
                "attempt_id": "attempt_1",
                "seq": 1,
            },
        ),
        new_runtime_event(
            event_type=event_type,
            context={
                "run_id": case.run_input.run_id or case.case_id,
                "attempt_id": "attempt_1",
                "seq": 2,
            },
        ),
    ]
    return AgentRunOutput(
        run_id=case.run_input.run_id or case.case_id,
        attempt_id="attempt_1",
        status=status,
        terminal_reason=terminal_reason,
        events=events,
    )


@pytest.mark.asyncio
async def test_run_dataset_builds_report_with_case_statuses() -> None:
    """Runner should aggregate case-level pass/fail into report counters."""
    cases = [
        DatasetCase(
            case_id="case_ok",
            description="completed flow",
            run_input=AgentRunInput(
                input="hello",
                run_id="run_case_ok",
                agent_id="agent",
                graph_preset="single_react",
            ),
            expected_status=RunStatus.COMPLETED,
            expected_terminal_reason=TerminalReason.FINAL_ANSWER,
        ),
        DatasetCase(
            case_id="case_fail",
            description="failed flow",
            run_input=AgentRunInput(
                input="hello",
                run_id="run_case_fail",
                agent_id="agent",
                graph_preset="single_react",
            ),
            expected_status=RunStatus.FAILED,
            expected_terminal_reason=TerminalReason.RUNTIME_ERROR,
        ),
    ]
    report = await run_dataset(
        cases=cases,
        run_executor=_fake_executor,
        candidate_id="candidate_local",
    )
    assert report.metadata["case_count"] == 2
    assert report.passed_cases == 2
    assert report.failed_cases == 0
