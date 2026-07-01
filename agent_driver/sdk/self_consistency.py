"""Self-consistency: run an agent N times and take the plurality-vote answer.

A generic runtime technique for beating per-task LLM non-determinism: sample the
SAME run several times and keep the answer the samples most agree on. It helps
exactly when a model is right *more often than any single wrong answer* even if
not on every run — the correct value tends to be the plurality while wrong
answers scatter, so voting recovers it.

This is intentionally model- and domain-agnostic. The caller supplies a ``key``
that maps an :class:`AgentRunOutput` to a comparable, hashable vote token (e.g.
"normalize the final number"); samples whose key is empty/``None`` abstain. The
output backing the winning token is returned, with the full vote distribution so
the caller can gate on confidence.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput


class _Runnable(Protocol):
    async def run(self, run_input: AgentRunInput, **kwargs: Any) -> AgentRunOutput: ...


def _default_key(output: AgentRunOutput) -> Hashable | None:
    """Default vote token: the trimmed final answer (empty → abstain)."""
    answer = (getattr(output, "answer", None) or "").strip()
    return answer or None


@dataclass(slots=True)
class SelfConsistencyResult:
    """Outcome of a self-consistency run."""

    consensus: AgentRunOutput | None
    """A representative output backing the winning vote token (None if every
    sample abstained or errored)."""
    consensus_key: Hashable | None
    votes: dict[Hashable, int]
    """token → vote count over the non-abstaining samples."""
    sample_count: int
    """How many samples were requested."""
    valid_count: int
    """How many samples produced a non-abstaining vote."""
    samples: list[AgentRunOutput] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Winning votes / valid votes (0.0 when nothing voted)."""
        if self.valid_count <= 0 or self.consensus_key is None:
            return 0.0
        return self.votes.get(self.consensus_key, 0) / self.valid_count

    @property
    def agreed(self) -> bool:
        """True when at least one sample produced a consensus answer."""
        return self.consensus is not None


def _clone_with_run_id(run_input: AgentRunInput, run_id: str) -> AgentRunInput:
    return run_input.model_copy(update={"run_id": run_id})


async def run_self_consistent(
    agent: _Runnable,
    run_input: AgentRunInput,
    *,
    samples: int = 5,
    key: Callable[[AgentRunOutput], Hashable | None] = _default_key,
    concurrency: int | None = None,
    completed_only: bool = True,
    **run_kwargs: Any,
) -> SelfConsistencyResult:
    """Run ``agent`` on ``run_input`` ``samples`` times and plurality-vote.

    Each sample runs with a distinct ``run_id`` (``"{base}__sc{i}"``) so the
    concurrent runs don't collide on the checkpoint/event stores. A sample
    abstains (does not vote) when it raises, when ``completed_only`` and its
    status isn't COMPLETED, or when ``key`` returns ``None``/empty. The winning
    token is the most-voted one; ties break toward the lowest sample index
    (deterministic). Returns the backing output as ``consensus``.

    ``concurrency`` bounds how many samples run at once (default: all).
    ``run_kwargs`` (e.g. ``tool_gate``) are forwarded to every ``agent.run``.
    """
    if samples < 1:
        raise ValueError("samples must be >= 1")

    base_id = run_input.run_id or "run"
    sem = asyncio.Semaphore(concurrency) if concurrency and concurrency > 0 else None

    async def _one(i: int) -> AgentRunOutput | BaseException:
        sample_input = _clone_with_run_id(run_input, f"{base_id}__sc{i}")
        try:
            if sem is not None:
                async with sem:
                    return await agent.run(sample_input, **run_kwargs)
            return await agent.run(sample_input, **run_kwargs)
        except BaseException as exc:  # noqa: BLE001 - a failed sample just abstains
            if isinstance(exc, asyncio.CancelledError):
                raise
            return exc

    results = await asyncio.gather(*[_one(i) for i in range(samples)])

    outputs: list[AgentRunOutput] = []
    errors: list[BaseException] = []
    votes: Counter[Hashable] = Counter()
    # First output seen per token → deterministic, lowest-index tie-break.
    first_for_key: dict[Hashable, AgentRunOutput] = {}

    for res in results:
        if isinstance(res, BaseException):
            errors.append(res)
            continue
        outputs.append(res)
        if completed_only and getattr(getattr(res, "status", None), "value", None) != "completed":
            continue
        token = key(res)
        if token is None or token == "":
            continue
        votes[token] += 1
        first_for_key.setdefault(token, res)

    consensus_key: Hashable | None = None
    consensus: AgentRunOutput | None = None
    if votes:
        # Counter.most_common is insertion-stable for ties; insertion order is
        # sample order, so this is the lowest-index winner on a tie.
        consensus_key = votes.most_common(1)[0][0]
        consensus = first_for_key.get(consensus_key)

    return SelfConsistencyResult(
        consensus=consensus,
        consensus_key=consensus_key,
        votes=dict(votes),
        sample_count=samples,
        valid_count=sum(votes.values()),
        samples=outputs,
        errors=errors,
    )


__all__ = ["SelfConsistencyResult", "run_self_consistent"]
