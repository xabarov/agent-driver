"""Phased supervisor/coordinator: research → synthesize (offline).

Coordination C4. The supervisor/orchestrator-worker topology (a lead fans work to
workers, joins, synthesizes) is the 2026 production default. `run_coordinator` promotes
it into a reusable, domain-neutral SDK primitive: an ordered list of phases, each of
which fans out subagents via `run_subagent_group` (real parallelism + a join policy) and
merges the results — the merged string threads into the next phase's `build_specs`, so a
`research` phase's findings become the `synthesize` phase's input. Agents can be resolved
from the C2 `AgentRegistry`; here they're inline specs to keep the example offline.

    python examples/cookbook/27_coordinator.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import (
    CoordinatorPhase,
    SubagentMergeMode,
    SubagentSpec,
    ToolSet,
    create_agent,
    run_coordinator,
)


async def main() -> int:
    parent = create_agent(
        provider=FakeProvider(response_text="finding"),
        tools=ToolSet.only(),
    )

    topics = ["pricing", "latency", "safety"]
    phases = [
        # Phase 1 — fan out one researcher per topic, join WAIT_ALL, APPEND findings.
        CoordinatorPhase(
            "research",
            lambda prior: [
                SubagentSpec(agent_type=f"researcher_{t}", prompt=f"Research {t}")
                for t in topics
            ],
        ),
        # Phase 2 — a single writer synthesizes the research phase's merged findings.
        CoordinatorPhase(
            "synthesize",
            lambda prior: [
                SubagentSpec(
                    agent_type="writer",
                    prompt=f"Write a brief from:\n{prior['research'].merged}",
                )
            ],
            merge_mode=SubagentMergeMode.SYNTHESIZE,
        ),
    ]

    result = await run_coordinator(
        parent,
        phases,
        synthesizer_provider=FakeProvider(response_text="Executive brief: all clear."),
    )

    for phase in result.phases:
        print(f"[{phase.name}] satisfied={phase.satisfied} "
              f"({phase.group.succeeded}/{len(phase.group.results)} ok)")
    print("satisfied:", result.satisfied)
    print("final:", result.final)
    return 0 if result.satisfied else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
