"""Phased supervisor/coordinator over the fan-out+join+merge primitives (C4)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agent_driver.sdk.group as group
from agent_driver.contracts.enums import (
    RunStatus,
    SubagentJoinPolicy,
    SubagentMergeMode,
)
from agent_driver.sdk import (
    CoordinatorPhase,
    CoordinatorResult,
    SubagentSpec,
    run_coordinator,
)
from agent_driver.sdk.subagent import SubagentResult

# agent_type -> (delay_seconds, RunStatus | Exception)
_SCRIPT: dict[str, object] = {}
_LIVE = {"now": 0, "max": 0}


def _result(agent_type: str, status: RunStatus, answer: str | None = None) -> SubagentResult:
    return SubagentResult(
        child_run_id=f"c-{agent_type}",
        parent_run_id="p",
        agent_type=agent_type,
        status=status,
        terminal_reason=None,
        answer=agent_type if answer is None else answer,
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
        return _result(spec.agent_type, outcome, answer=spec.prompt)
    finally:
        _LIVE["now"] -= 1


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _SCRIPT.clear()
    _LIVE.update(now=0, max=0)
    monkeypatch.setattr(group, "run_subagent", _fake_run_subagent)


def _spec(name: str, prompt: str | None = None) -> SubagentSpec:
    return SubagentSpec(agent_type=name, prompt=prompt or name)


@pytest.mark.asyncio
async def test_phases_run_in_order_and_thread_merged_output() -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.COMPLETED),
        b=(0.001, RunStatus.COMPLETED),
        writer=(0.001, RunStatus.COMPLETED),
    )
    seen: dict[str, str] = {}

    def build_synth(prior):  # noqa: ANN001, ANN202
        # Prior phase's merged output is visible and threads into this phase's spec.
        seen["research"] = prior["research"].merged
        return [_spec("writer", prompt=f"write:{prior['research'].merged[:3]}")]

    phases = [
        CoordinatorPhase(
            "research",
            lambda prior: [_spec("a", "aa"), _spec("b", "bb")],
        ),
        CoordinatorPhase("synthesize", build_synth),
    ]
    res = await run_coordinator(None, phases)
    assert isinstance(res, CoordinatorResult)
    assert [p.name for p in res.phases] == ["research", "synthesize"]
    assert res.satisfied
    # research merged (APPEND, labeled) contains both children's answers
    assert "aa" in seen["research"] and "bb" in seen["research"]
    # final is the synthesize phase's merged single-writer output
    assert "write:" in res.final
    assert res.phase("research").group.succeeded == 2


@pytest.mark.asyncio
async def test_fan_out_within_a_phase_is_concurrent() -> None:
    _SCRIPT.update(
        a=(0.03, RunStatus.COMPLETED),
        b=(0.03, RunStatus.COMPLETED),
        c=(0.03, RunStatus.COMPLETED),
    )
    phases = [
        CoordinatorPhase("fan", lambda prior: [_spec("a"), _spec("b"), _spec("c")]),
    ]
    await run_coordinator(None, phases)
    assert _LIVE["max"] >= 2  # ran concurrently, not one-at-a-time


@pytest.mark.asyncio
async def test_unsatisfied_phase_halts_pipeline_by_default() -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.FAILED),  # WAIT_ALL not satisfied
        never=(0.001, RunStatus.COMPLETED),
    )
    ran_second = {"v": False}

    def build_second(prior):  # noqa: ANN001, ANN202
        ran_second["v"] = True
        return [_spec("never")]

    phases = [
        CoordinatorPhase("first", lambda prior: [_spec("a")]),
        CoordinatorPhase("second", build_second),
    ]
    res = await run_coordinator(None, phases)
    assert res.stopped_early is True
    assert not res.satisfied
    assert ran_second["v"] is False  # second phase never built/ran
    assert [p.name for p in res.phases] == ["first"]


@pytest.mark.asyncio
async def test_continue_on_unsatisfied_when_disabled() -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.FAILED),
        b=(0.001, RunStatus.COMPLETED),
    )
    phases = [
        CoordinatorPhase("first", lambda prior: [_spec("a")]),
        CoordinatorPhase("second", lambda prior: [_spec("b")]),
    ]
    res = await run_coordinator(None, phases, stop_on_unsatisfied=False)
    assert res.stopped_early is False
    assert [p.name for p in res.phases] == ["first", "second"]
    assert res.satisfied is False  # a phase still unsatisfied overall


@pytest.mark.asyncio
async def test_async_build_specs_is_awaited() -> None:
    _SCRIPT.update(x=(0.001, RunStatus.COMPLETED))

    async def build(prior):  # noqa: ANN001, ANN202
        await asyncio.sleep(0)
        return [_spec("x", "xx")]

    res = await run_coordinator(None, [CoordinatorPhase("p", build)])
    assert res.satisfied
    assert "xx" in res.final


@pytest.mark.asyncio
async def test_empty_phase_is_satisfied_and_empty() -> None:
    res = await run_coordinator(None, [CoordinatorPhase("p", lambda prior: [])])
    assert res.satisfied
    assert res.final == ""
    assert res.phase("p").group.succeeded == 0


@pytest.mark.asyncio
async def test_k_of_n_join_policy_forwarded() -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.COMPLETED),
        b=(0.001, RunStatus.FAILED),
        c=(0.001, RunStatus.COMPLETED),
    )
    phases = [
        CoordinatorPhase(
            "quorum",
            lambda prior: [_spec("a"), _spec("b"), _spec("c")],
            join_policy=SubagentJoinPolicy.K_OF_N,
            k=2,
        ),
    ]
    res = await run_coordinator(None, phases)
    assert res.satisfied  # 2 of 3 completed meets k=2


@pytest.mark.asyncio
async def test_synthesize_phase_uses_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _SCRIPT.update(
        a=(0.001, RunStatus.COMPLETED),
        b=(0.001, RunStatus.COMPLETED),
    )
    calls = {"n": 0}

    async def _fake_aux(**kwargs):  # noqa: ANN003, ANN202
        calls["n"] += 1
        return SimpleNamespace(message=SimpleNamespace(content="SYNTH"))

    monkeypatch.setattr("agent_driver.llm.aux.aux_completion", _fake_aux)
    phases = [
        CoordinatorPhase(
            "synth",
            lambda prior: [_spec("a", "aa"), _spec("b", "bb")],
            merge_mode=SubagentMergeMode.SYNTHESIZE,
        ),
    ]
    res = await run_coordinator(None, phases, synthesizer_provider=object())
    assert calls["n"] == 1
    assert res.final == "SYNTH"


@pytest.mark.asyncio
async def test_synthesize_without_provider_raises() -> None:
    phases = [
        CoordinatorPhase(
            "synth",
            lambda prior: [_spec("a")],
            merge_mode=SubagentMergeMode.SYNTHESIZE,
        ),
    ]
    with pytest.raises(ValueError, match="synthesizer_provider"):
        await run_coordinator(None, phases)
