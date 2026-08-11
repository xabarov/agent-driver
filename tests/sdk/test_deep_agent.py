"""Deep-agent driver: plan → fan out → artifacts → synthesize (coordination C5)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_driver.sdk.deep_agent as deep_agent
import agent_driver.sdk.group as group
from agent_driver.contracts.enums import RunStatus
from agent_driver.sdk import DeepAgentResult, SubagentSpec, run_deep_agent
from agent_driver.sdk.subagent import SubagentResult

# spec.agent_type -> RunStatus for fanned-out workers
_WORKER_STATUS: dict[str, RunStatus] = {}
_SYNTH_PROMPTS: list[str] = []
_FANOUT_SPECS: list[SubagentSpec] = []


def _result(agent_type: str, status: RunStatus, answer: str | None) -> SubagentResult:
    return SubagentResult(
        child_run_id=f"c-{agent_type}",
        parent_run_id="p",
        agent_type=agent_type,
        status=status,
        terminal_reason=None,
        answer=answer,
        structured_output=None,
        tool_trace=(),
        usage=None,
        raw_output=None,
    )


async def _fake_fanout(parent, spec, **_kw):  # noqa: ANN001, ANN201
    _FANOUT_SPECS.append(spec)
    status = _WORKER_STATUS.get(spec.agent_type, RunStatus.COMPLETED)
    return _result(spec.agent_type, status, f"findings for {spec.prompt}")


async def _fake_synth(parent, spec, **_kw):  # noqa: ANN001, ANN201
    _SYNTH_PROMPTS.append(spec.prompt)
    return _result(spec.agent_type, RunStatus.COMPLETED, "FINAL BRIEF")


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _WORKER_STATUS.clear()
    _SYNTH_PROMPTS.clear()
    _FANOUT_SPECS.clear()
    monkeypatch.setattr(group, "run_subagent", _fake_fanout)
    monkeypatch.setattr(deep_agent, "run_subagent", _fake_synth)


@pytest.mark.asyncio
async def test_full_loop_plans_fans_out_captures_and_synthesizes(tmp_path: Path) -> None:
    res = await run_deep_agent(
        None,
        "Assess the launch",
        workspace_cwd=tmp_path,
        planner=lambda task: ["research pricing", "research latency"],
    )
    assert isinstance(res, DeepAgentResult)
    assert res.plan.subtasks == ("research pricing", "research latency")
    assert res.satisfied and res.answer == "FINAL BRIEF"
    # plan + two worker artifacts landed on the shared workspace
    assert (tmp_path / "plan.md").read_text().count("research") == 2
    assert {p.name for p in (tmp_path / "artifacts").glob("*.md")} == {
        "00_worker_00.md",
        "01_worker_01.md",
    }
    assert len(res.artifacts) == 2


@pytest.mark.asyncio
async def test_workers_share_the_workspace(tmp_path: Path) -> None:
    await run_deep_agent(
        None, "t", workspace_cwd=tmp_path, planner=lambda task: ["a", "b"]
    )
    # every fanned-out worker spec carries the shared workspace_cwd
    assert _FANOUT_SPECS  # workers ran
    assert all(
        s.app_metadata.get("workspace_cwd") == str(tmp_path) for s in _FANOUT_SPECS
    )


@pytest.mark.asyncio
async def test_synthesizer_gets_refs_not_full_concatenation(tmp_path: Path) -> None:
    await run_deep_agent(
        None, "t", workspace_cwd=tmp_path, planner=lambda task: ["one", "two"]
    )
    assert len(_SYNTH_PROMPTS) == 1
    prompt = _SYNTH_PROMPTS[0]
    # the synthesizer is handed artifact PATHS (references), and shares the workspace
    assert "artifacts/00_worker_00.md" in prompt
    assert "artifacts/01_worker_01.md" in prompt


@pytest.mark.asyncio
async def test_empty_plan_returns_early_unsatisfied(tmp_path: Path) -> None:
    res = await run_deep_agent(
        None, "t", workspace_cwd=tmp_path, planner=lambda task: []
    )
    assert res.plan.subtasks == ()
    assert res.satisfied is False and res.answer == ""
    assert res.group.results == ()
    assert not _SYNTH_PROMPTS  # no synthesizer ran
    assert (tmp_path / "plan.md").exists()  # plan doc still written


@pytest.mark.asyncio
async def test_custom_worker_spec_builder(tmp_path: Path) -> None:
    def build(index: int, subtask: str) -> SubagentSpec:
        return SubagentSpec(agent_type=f"custom_{index}", prompt=f"DO: {subtask}")

    res = await run_deep_agent(
        None,
        "t",
        workspace_cwd=tmp_path,
        planner=lambda task: ["x"],
        worker_spec=build,
    )
    assert res.satisfied
    assert _FANOUT_SPECS[0].agent_type == "custom_0"
    assert _FANOUT_SPECS[0].prompt == "DO: x"
    assert (tmp_path / "artifacts" / "00_custom_0.md").exists()


@pytest.mark.asyncio
async def test_include_partial_salvages_failed_worker(tmp_path: Path) -> None:
    _WORKER_STATUS["worker_01"] = RunStatus.FAILED
    res = await run_deep_agent(
        None,
        "t",
        workspace_cwd=tmp_path,
        planner=lambda task: ["a", "b"],
        include_partial=True,
    )
    # both workers' findings captured, including the failed one's partial
    assert {a.path for a in res.artifacts} == {
        "artifacts/00_worker_00.md",
        "artifacts/01_worker_01.md",
    }


@pytest.mark.asyncio
async def test_failed_worker_dropped_without_include_partial(tmp_path: Path) -> None:
    _WORKER_STATUS["worker_01"] = RunStatus.FAILED
    res = await run_deep_agent(
        None,
        "t",
        workspace_cwd=tmp_path,
        planner=lambda task: ["a", "b"],
        include_partial=False,
    )
    assert {a.path for a in res.artifacts} == {"artifacts/00_worker_00.md"}


@pytest.mark.asyncio
async def test_llm_planner_parses_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_aux(**_kw):  # noqa: ANN003, ANN202
        return SimpleNamespace(
            message=SimpleNamespace(content="1. alpha\n2. beta\n- gamma\n\n")
        )

    monkeypatch.setattr("agent_driver.llm.aux.aux_completion", _fake_aux)
    res = await run_deep_agent(
        None, "t", workspace_cwd=tmp_path, planner_provider=object(), max_subtasks=8
    )
    assert res.plan.subtasks == ("alpha", "beta", "gamma")


@pytest.mark.asyncio
async def test_max_subtasks_truncates(tmp_path: Path) -> None:
    res = await run_deep_agent(
        None,
        "t",
        workspace_cwd=tmp_path,
        planner=lambda task: ["a", "b", "c", "d"],
        max_subtasks=2,
    )
    assert res.plan.subtasks == ("a", "b")


@pytest.mark.asyncio
async def test_requires_a_planner(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="planner"):
        await run_deep_agent(None, "t", workspace_cwd=tmp_path)
