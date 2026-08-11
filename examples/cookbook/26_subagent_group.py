"""Subagent group: concurrent fan-out with a join policy (offline).

Coordination C1. Run N subagents concurrently under a concurrency cap and join them
with a policy from the shared `SubagentJoinPolicy` vocabulary — `WAIT_ALL`,
`WAIT_ANY` (first success wins, cancel the rest), `K_OF_N`, `RACE`, or
`BEST_EFFORT_UNTIL_DEADLINE` — instead of hand-rolling `asyncio.gather` + a semaphore.
A failed child never aborts the group; results come back aligned to the input specs.

    python examples/cookbook/26_subagent_group.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import (
    SubagentJoinPolicy,
    SubagentMergeMode,
    SubagentSpec,
    ToolSet,
    create_agent,
    merge_subagent_results,
    run_subagent_group,
    synthesize_subagent_results,
)


async def main() -> int:
    parent = create_agent(
        provider=FakeProvider(response_text="analysis complete"),
        tools=ToolSet.only(),
    )
    specs = [
        SubagentSpec(agent_type=f"worker_{i}", prompt=f"Analyze slice {i}")
        for i in range(4)
    ]

    result = await run_subagent_group(
        parent,
        specs,
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        concurrency=2,  # at most 2 children run at once
    )
    print("satisfied:", result.satisfied)
    print("succeeded:", result.succeeded, "failed:", result.failed)

    # Merge the joined results — deterministically (APPEND/RANK/VOTE)...
    print("append:", merge_subagent_results(result.results, mode=SubagentMergeMode.APPEND))
    # ...or synthesize them into one answer with a model (the real SYNTHESIZE).
    synthesized = await synthesize_subagent_results(
        result.results,
        provider=FakeProvider(response_text="All four slices agree: complete."),
    )
    print("synthesized:", synthesized)
    return result.succeeded


if __name__ == "__main__":
    asyncio.run(main())
