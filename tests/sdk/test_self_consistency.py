"""Self-consistency / sample-and-vote primitive."""

from __future__ import annotations

import re

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.sdk import SelfConsistencyResult, run_self_consistent


class _Status:
    def __init__(self, value: str) -> None:
        self.value = value


class _Out:
    """Duck-typed AgentRunOutput stand-in (the primitive reads answer + status)."""

    def __init__(self, answer: str | None, status: str = "completed") -> None:
        self.answer = answer
        self.status = _Status(status)


class _ScriptedAgent:
    """Returns a scripted output per sample, keyed by the ``__sc{i}`` run_id."""

    def __init__(self, scripted: list) -> None:
        self._scripted = scripted
        self.seen_run_ids: list[str] = []

    async def run(self, run_input: AgentRunInput, **_kw):
        self.seen_run_ids.append(run_input.run_id)
        i = int(run_input.run_id.rsplit("__sc", 1)[-1])
        item = self._scripted[i]
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, _Out) else _Out(item)


def _input() -> AgentRunInput:
    return AgentRunInput(
        input="what % is West?",
        run_id="base",
        agent_id="agent",
        graph_preset="single_react",
    )


@pytest.mark.asyncio
async def test_plurality_winner_even_when_below_half() -> None:
    """The correct answer wins as the plurality even when scattered wrong
    answers mean it's a minority of the total — the core of why voting helps."""
    agent = _ScriptedAgent(["16%", "24%", "16%", "99%", "16%"])
    res = await run_self_consistent(agent, _input(), samples=5)
    assert isinstance(res, SelfConsistencyResult)
    assert res.consensus_key == "16%"
    assert res.consensus.answer == "16%"
    assert res.votes == {"16%": 3, "24%": 1, "99%": 1}
    assert res.valid_count == 5
    assert res.confidence == 3 / 5
    assert res.agreed is True
    # Distinct run_ids per sample (no checkpoint/event collision).
    assert sorted(agent.seen_run_ids) == [f"base__sc{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_normalizing_key_groups_equivalent_answers() -> None:
    """A caller key that normalizes formatting lets '16%', '16 %' and '**16%**'
    vote together — the domain knob excel-ai needs for numeric answers."""
    agent = _ScriptedAgent(["16%", "16 %", "**16%**", "twenty", "16!"])
    digits = lambda o: re.sub(r"\D", "", o.answer or "") or None  # noqa: E731
    res = await run_self_consistent(agent, _input(), samples=5, key=digits)
    assert res.consensus_key == "16"
    assert res.votes["16"] == 4  # four "16" variants agree


@pytest.mark.asyncio
async def test_errors_and_failed_status_abstain() -> None:
    """A raising sample and a non-completed sample don't vote; the error is
    recorded, and the consensus comes from the valid samples only."""
    agent = _ScriptedAgent(
        ["7", RuntimeError("boom"), _Out("9", status="failed"), "7", "7"]
    )
    res = await run_self_consistent(agent, _input(), samples=5)
    assert res.consensus_key == "7"
    assert res.votes == {"7": 3}
    assert res.valid_count == 3  # error + failed abstain
    assert len(res.errors) == 1
    assert res.confidence == 1.0  # 3/3 of the valid votes


@pytest.mark.asyncio
async def test_empty_answers_abstain_and_no_consensus() -> None:
    agent = _ScriptedAgent(["", None, "  "])
    res = await run_self_consistent(agent, _input(), samples=3)
    assert res.consensus is None
    assert res.consensus_key is None
    assert res.agreed is False
    assert res.confidence == 0.0


@pytest.mark.asyncio
async def test_tie_breaks_to_lowest_sample_index() -> None:
    agent = _ScriptedAgent(["B", "A", "A", "B"])  # 2-2 tie
    res = await run_self_consistent(agent, _input(), samples=4)
    # "B" first appears at sample 0 → wins the tie deterministically.
    assert res.consensus_key == "B"


@pytest.mark.asyncio
async def test_concurrency_bound_still_votes() -> None:
    agent = _ScriptedAgent(["5", "5", "6", "5"])
    res = await run_self_consistent(agent, _input(), samples=4, concurrency=2)
    assert res.consensus_key == "5"
    assert res.votes["5"] == 3


@pytest.mark.asyncio
async def test_samples_must_be_positive() -> None:
    with pytest.raises(ValueError, match="samples must be"):
        await run_self_consistent(_ScriptedAgent([]), _input(), samples=0)
