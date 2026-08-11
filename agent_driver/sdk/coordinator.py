"""Phased supervisor / orchestrator-worker coordinator (coordination C4).

The supervisor topology — a lead that fans work out to workers, joins, and
synthesizes — is the 2026 production default, but agent-driver only had the
*primitives* (fan-out+join in :func:`run_subagent_group`, merge/synthesize, an
agent registry), so a consumer hand-wired the loop. This composes them into one
reusable, domain-neutral coordinator:

    phases = [
        CoordinatorPhase("research", lambda prior: [explorer_spec(t) for t in topics]),
        CoordinatorPhase(
            "synthesize",
            lambda prior: [writer_spec(prior["research"].merged)],
            merge_mode=SubagentMergeMode.SYNTHESIZE,
        ),
    ]
    result = await run_coordinator(parent, phases, synthesizer_provider=provider)
    print(result.final)

Each phase builds its worker specs (optionally from prior phases' merged output),
fans them out concurrently under a join policy, and merges the results — the merged
string threads to the next phase. Real parallelism (via ``run_subagent_group``),
real LLM synthesis (``SYNTHESIZE``), agents resolvable from the C2 registry.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from agent_driver.contracts.enums import SubagentJoinPolicy, SubagentMergeMode
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent
from agent_driver.sdk.group import SubagentGroupResult, run_subagent_group
from agent_driver.sdk.merge import merge_subagent_results, synthesize_subagent_results
from agent_driver.sdk.subagent import SubagentSpec

# A phase's spec builder sees prior phases' results (by name) and returns this phase's
# worker specs — sync or async (so a phase can plan its fan-out with an LLM call first).
SpecBuilder = Callable[
    [Mapping[str, "PhaseResult"]],
    Sequence[SubagentSpec] | Awaitable[Sequence[SubagentSpec]],
]


@dataclass(frozen=True, slots=True)
class CoordinatorPhase:
    """One supervisor phase: fan out workers, join, merge."""

    name: str
    build_specs: SpecBuilder
    join_policy: SubagentJoinPolicy = SubagentJoinPolicy.WAIT_ALL
    merge_mode: SubagentMergeMode = SubagentMergeMode.APPEND
    concurrency: int | None = None
    k: int | None = None
    retries: int = 0
    include_partial: bool = False


@dataclass(frozen=True, slots=True)
class PhaseResult:
    """The outcome of one phase: its group result plus the merged string."""

    name: str
    group: SubagentGroupResult
    merged: str

    @property
    def satisfied(self) -> bool:
        """Whether the phase's join policy was satisfied."""
        return self.group.satisfied


@dataclass(frozen=True, slots=True)
class CoordinatorResult:
    """The outcome of a coordinator run — every phase, in order."""

    phases: tuple[PhaseResult, ...] = field(default_factory=tuple)
    stopped_early: bool = False

    @property
    def final(self) -> str:
        """The last phase's merged output (``""`` if no phase ran)."""
        return self.phases[-1].merged if self.phases else ""

    @property
    def satisfied(self) -> bool:
        """True when every phase that ran was satisfied and none stopped early."""
        return not self.stopped_early and all(p.satisfied for p in self.phases)

    def phase(self, name: str) -> PhaseResult | None:
        """Look up a phase result by name."""
        return next((p for p in self.phases if p.name == name), None)


async def _merge_phase(
    phase: CoordinatorPhase,
    group: SubagentGroupResult,
    *,
    synthesizer_provider: object | None,
) -> str:
    if phase.merge_mode == SubagentMergeMode.SYNTHESIZE:
        return await synthesize_subagent_results(
            group.results,
            provider=synthesizer_provider,
            include_partial=phase.include_partial,
        )
    return merge_subagent_results(
        group.results, mode=phase.merge_mode, include_partial=phase.include_partial
    )


async def run_coordinator(
    parent: Agent,
    phases: Sequence[CoordinatorPhase],
    *,
    synthesizer_provider: object | None = None,
    stop_on_unsatisfied: bool = True,
    tool_gate: ToolGate | None = None,
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
) -> CoordinatorResult:
    """Run an ordered list of supervisor phases, threading each phase's merged output.

    For each phase: ``build_specs(prior)`` (given the prior phases' results by name,
    sync or async) yields the worker specs; they fan out concurrently via
    :func:`run_subagent_group` under the phase's ``join_policy`` / ``concurrency`` /
    ``k`` / ``retries``; the group is merged per ``merge_mode`` (``SYNTHESIZE`` needs
    ``synthesizer_provider``). The ``PhaseResult`` is added to ``prior`` for the next
    phase. With ``stop_on_unsatisfied`` (default), a phase whose join policy wasn't
    satisfied halts the pipeline and marks the result ``stopped_early``. A phase that
    builds zero specs is satisfied-and-empty. ``tool_gate`` / ``parent_run_id`` /
    ``parent_abort_handle`` forward to every child.
    """
    if synthesizer_provider is None and any(
        phase.merge_mode == SubagentMergeMode.SYNTHESIZE for phase in phases
    ):
        raise ValueError(
            "a SYNTHESIZE phase requires synthesizer_provider=... on run_coordinator"
        )

    prior: dict[str, PhaseResult] = {}
    ordered: list[PhaseResult] = []
    stopped_early = False
    for phase in phases:
        built = phase.build_specs(prior)
        specs = list(await built if inspect.isawaitable(built) else built)
        group = await run_subagent_group(
            parent,
            specs,
            join_policy=phase.join_policy,
            concurrency=phase.concurrency,
            k=phase.k,
            retries=phase.retries,
            tool_gate=tool_gate,
            parent_run_id=parent_run_id,
            parent_abort_handle=parent_abort_handle,
        )
        merged = await _merge_phase(
            phase, group, synthesizer_provider=synthesizer_provider
        )
        result = PhaseResult(name=phase.name, group=group, merged=merged)
        ordered.append(result)
        prior[phase.name] = result
        if stop_on_unsatisfied and not group.satisfied:
            stopped_early = True
            break

    return CoordinatorResult(phases=tuple(ordered), stopped_early=stopped_early)


__all__ = [
    "CoordinatorPhase",
    "CoordinatorResult",
    "PhaseResult",
    "run_coordinator",
]
