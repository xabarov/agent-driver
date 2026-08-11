"""Live coordination events — on_event streaming (coordination observability)."""

from __future__ import annotations

import asyncio
import logging

import pytest

import agent_driver.sdk.deep_agent as deep_agent
import agent_driver.sdk.group as group
from agent_driver.contracts.enums import RunStatus, SubagentJoinPolicy
from agent_driver.sdk import (
    CoordinationEvent,
    CoordinatorPhase,
    SubagentSpec,
    log_coordination_events,
    run_coordinator,
    run_deep_agent,
    run_subagent_group,
)
from agent_driver.sdk.subagent import SubagentResult

_SCRIPT: dict[str, object] = {}
_ATTEMPTS: dict[str, int] = {}


def _result(agent_type: str, status: RunStatus) -> SubagentResult:
    return SubagentResult(
        child_run_id=f"c-{agent_type}",
        parent_run_id="p",
        agent_type=agent_type,
        status=status,
        terminal_reason=None,
        answer=agent_type,
        structured_output=None,
        tool_trace=(),
        usage=None,
        raw_output=None,
    )


async def _fake_run_subagent(parent, spec, **_kw):  # noqa: ANN001, ANN201
    prior = _ATTEMPTS.get(spec.agent_type, 0)
    _ATTEMPTS[spec.agent_type] = prior + 1
    script = _SCRIPT.get(spec.agent_type, RunStatus.COMPLETED)
    outcome = script[min(prior, len(script) - 1)] if isinstance(script, list) else script
    await asyncio.sleep(0)
    if isinstance(outcome, BaseException):
        raise outcome
    return _result(spec.agent_type, outcome)


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _SCRIPT.clear()
    _ATTEMPTS.clear()
    monkeypatch.setattr(group, "run_subagent", _fake_run_subagent)
    monkeypatch.setattr(deep_agent, "run_subagent", _fake_run_subagent)


def _specs(*names: str) -> list[SubagentSpec]:
    return [SubagentSpec(agent_type=n, prompt=n) for n in names]


def _kinds(events: list[CoordinationEvent]) -> list[str]:
    return [e.kind for e in events]


@pytest.mark.asyncio
async def test_group_emits_lifecycle_events() -> None:
    events: list[CoordinationEvent] = []
    await run_subagent_group(None, _specs("a", "b", "c"), on_event=events.append, phase="p")
    kinds = _kinds(events)
    assert kinds[0] == "group_started" and kinds[-1] == "group_completed"
    assert kinds.count("child_started") == 3 and kinds.count("child_completed") == 3
    # every event carries the phase label; completions carry status + the raw result
    assert all(e.phase == "p" for e in events)
    done = [e for e in events if e.kind == "child_completed"]
    assert {e.agent_type for e in done} == {"a", "b", "c"}
    assert all(e.status == "completed" and e.result is not None for e in done)
    assert events[0].total == 3


@pytest.mark.asyncio
async def test_child_completed_reports_failure_status() -> None:
    _SCRIPT["bad"] = RunStatus.FAILED
    events: list[CoordinationEvent] = []
    await run_subagent_group(None, _specs("bad"), on_event=events.append)
    done = next(e for e in events if e.kind == "child_completed")
    assert done.status == "failed"


@pytest.mark.asyncio
async def test_retry_emits_child_retrying() -> None:
    _SCRIPT["flaky"] = [RunStatus.FAILED, RunStatus.COMPLETED]  # fail once, then pass
    events: list[CoordinationEvent] = []
    await run_subagent_group(None, _specs("flaky"), retries=1, on_event=events.append)
    kinds = _kinds(events)
    assert kinds.count("child_retrying") == 1
    assert kinds.count("child_completed") == 1  # one final completion after the retry


@pytest.mark.asyncio
async def test_empty_group_still_brackets_events() -> None:
    events: list[CoordinationEvent] = []
    await run_subagent_group(None, [], on_event=events.append)
    assert _kinds(events) == ["group_started", "group_completed"]


@pytest.mark.asyncio
async def test_coordinator_emits_phase_events_with_labels() -> None:
    events: list[CoordinationEvent] = []
    phases = [
        CoordinatorPhase("research", lambda prior: _specs("r1", "r2")),
        CoordinatorPhase("write", lambda prior: _specs("w1")),
    ]
    await run_coordinator(None, phases, on_event=events.append)
    kinds = _kinds(events)
    assert kinds.count("phase_started") == 2 and kinds.count("phase_completed") == 2
    # a research-phase child carries phase='research'
    research_children = [
        e for e in events if e.kind == "child_completed" and e.phase == "research"
    ]
    assert {e.agent_type for e in research_children} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_deep_agent_emits_plan_and_synthesis_events() -> None:
    events: list[CoordinationEvent] = []
    await run_deep_agent(
        None,
        "task",
        workspace_cwd="/tmp/coord_events_ws",
        planner=lambda task: ["sub a", "sub b"],
        on_event=events.append,
    )
    kinds = _kinds(events)
    assert "plan_ready" in kinds
    assert "synthesis_started" in kinds and "synthesis_completed" in kinds
    plan = next(e for e in events if e.kind == "plan_ready")
    assert plan.total == 2
    # workers fan out under the 'workers' phase
    assert any(e.kind == "child_completed" and e.phase == "workers" for e in events)


@pytest.mark.asyncio
async def test_observer_that_raises_does_not_break_the_run() -> None:
    def _boom(_e: CoordinationEvent) -> None:
        raise RuntimeError("observer bug")

    res = await run_subagent_group(None, _specs("a", "b"), on_event=_boom)
    assert res.succeeded == 2  # the run completed despite the observer raising


@pytest.mark.asyncio
async def test_log_coordination_events_observer(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="agent_driver.coordination"):
        await run_subagent_group(
            None, _specs("a"), on_event=log_coordination_events()
        )
    text = "\n".join(caplog.messages)
    assert "[coord]" in text and "starting" in text  # a live line was logged
