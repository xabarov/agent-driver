"""Concurrent subagent fan-out + join policies (coordination C1)."""

from __future__ import annotations

import asyncio

import pytest

import agent_driver.sdk.group as group
from agent_driver.contracts.enums import RunStatus, SubagentJoinPolicy
from agent_driver.sdk import SubagentSpec, run_subagent_group
from agent_driver.sdk.subagent import SubagentResult

# agent_type -> (delay_seconds, RunStatus | Exception)
_SCRIPT: dict[str, tuple[float, object]] = {}
_LIVE = {"now": 0, "max": 0}


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
    _LIVE["now"] += 1
    _LIVE["max"] = max(_LIVE["max"], _LIVE["now"])
    try:
        delay, outcome = _SCRIPT[spec.agent_type]
        await asyncio.sleep(delay)
        if isinstance(outcome, BaseException):
            raise outcome
        return _result(spec.agent_type, outcome)
    finally:
        _LIVE["now"] -= 1


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _SCRIPT.clear()
    _LIVE.update(now=0, max=0)
    monkeypatch.setattr(group, "run_subagent", _fake_run_subagent)


def _specs(*names: str) -> list[SubagentSpec]:
    return [SubagentSpec(agent_type=n, prompt=n) for n in names]


@pytest.mark.asyncio
async def test_wait_all_awaits_every_child_in_spec_order() -> None:
    _SCRIPT.update(
        a=(0.02, RunStatus.COMPLETED),
        b=(0.001, RunStatus.COMPLETED),
        c=(0.01, RunStatus.COMPLETED),
    )
    res = await run_subagent_group(
        None, _specs("a", "b", "c"), join_policy=SubagentJoinPolicy.WAIT_ALL
    )
    assert res.satisfied
    assert res.succeeded == 3
    assert [r.agent_type for r in res.results] == ["a", "b", "c"]  # aligned to specs


@pytest.mark.asyncio
async def test_wait_all_tolerates_failure_and_exception() -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.COMPLETED),
        b=(0.001, RunStatus.FAILED),
        c=(0.001, RuntimeError("boom")),
    )
    res = await run_subagent_group(None, _specs("a", "b", "c"))
    assert not res.satisfied  # WAIT_ALL wants all completed
    assert res.succeeded == 1 and res.failed == 2
    assert res.results[1].status == RunStatus.FAILED
    assert isinstance(res.errors[2], RuntimeError)


@pytest.mark.asyncio
async def test_wait_any_returns_on_first_success_and_cancels_rest() -> None:
    _SCRIPT.update(
        fast=(0.001, RunStatus.COMPLETED),
        slow1=(5.0, RunStatus.COMPLETED),
        slow2=(5.0, RunStatus.COMPLETED),
    )
    res = await run_subagent_group(
        None, _specs("fast", "slow1", "slow2"), join_policy=SubagentJoinPolicy.WAIT_ANY
    )
    assert res.satisfied and res.succeeded == 1
    assert res.results[0].agent_type == "fast"
    assert res.results[1] is None and res.results[2] is None  # cancelled


@pytest.mark.asyncio
async def test_k_of_n_returns_after_k_successes() -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.COMPLETED),
        b=(0.005, RunStatus.COMPLETED),
        c=(5.0, RunStatus.COMPLETED),
    )
    res = await run_subagent_group(
        None, _specs("a", "b", "c"), join_policy=SubagentJoinPolicy.K_OF_N, k=2
    )
    assert res.satisfied and res.succeeded == 2
    assert res.results[2] is None  # third cancelled once k=2 met


@pytest.mark.asyncio
async def test_k_of_n_requires_k() -> None:
    with pytest.raises(ValueError):
        await run_subagent_group(
            None, _specs("a"), join_policy=SubagentJoinPolicy.K_OF_N
        )


@pytest.mark.asyncio
async def test_race_first_to_finish_wins_even_on_failure() -> None:
    _SCRIPT.update(
        quickfail=(0.001, RunStatus.FAILED),
        slow=(5.0, RunStatus.COMPLETED),
    )
    res = await run_subagent_group(
        None, _specs("quickfail", "slow"), join_policy=SubagentJoinPolicy.RACE
    )
    assert res.satisfied  # a result arrived
    assert res.results[0].status == RunStatus.FAILED
    assert res.results[1] is None  # loser cancelled


@pytest.mark.asyncio
async def test_best_effort_takes_what_finished_by_deadline() -> None:
    _SCRIPT.update(
        fast=(0.001, RunStatus.COMPLETED),
        slow=(5.0, RunStatus.COMPLETED),
    )
    res = await run_subagent_group(
        None,
        _specs("fast", "slow"),
        join_policy=SubagentJoinPolicy.BEST_EFFORT_UNTIL_DEADLINE,
        deadline_seconds=0.1,
    )
    assert res.succeeded == 1
    assert res.results[0].agent_type == "fast"
    assert res.results[1] is None  # missed the deadline, cancelled


@pytest.mark.asyncio
async def test_concurrency_cap_limits_simultaneous_children() -> None:
    for name in ("a", "b", "c", "d"):
        _SCRIPT[name] = (0.03, RunStatus.COMPLETED)
    res = await run_subagent_group(
        None, _specs("a", "b", "c", "d"), concurrency=2
    )
    assert res.succeeded == 4
    assert _LIVE["max"] <= 2  # never more than 2 ran at once


@pytest.mark.asyncio
async def test_empty_group_is_trivially_satisfied() -> None:
    res = await run_subagent_group(None, [], join_policy=SubagentJoinPolicy.WAIT_ALL)
    assert res.satisfied and res.succeeded == 0 and res.results == ()
