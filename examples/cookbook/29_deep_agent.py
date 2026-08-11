"""Deep agent: one driver for plan → fan out → artifacts → synthesize (offline).

Coordination C5. `run_deep_agent` is the long-horizon "ultra agent" as a single
domain-neutral driver. It decomposes a task into independent subtasks (an LLM planner, or
a supplied decomposition), fans out one worker per subtask on a shared workspace, captures
each worker's findings as an artifact, and hands the synthesizer the compact references —
not the full concatenated findings — to write the final answer. The plan is persisted to
`<workspace>/plan.md` and each worker's output to `<workspace>/artifacts/`.

This composes the whole C-track: C1 fan-out+join, C2 registry-resolvable agents, C4
coordinator semantics, C5 artifact pattern. Context compaction applies for free inside
each child's run loop.

    python examples/cookbook/29_deep_agent.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent, run_deep_agent


async def main() -> int:
    parent = create_agent(
        provider=FakeProvider(response_text="Executive brief: the launch is on track."),
        tools=ToolSet.only(),
    )
    workspace = Path(tempfile.mkdtemp(prefix="deep_agent_"))

    # A planner could be an LLM (pass planner_provider=...); here it's a supplied
    # decomposition to keep the example offline and deterministic.
    result = await run_deep_agent(
        parent,
        "Assess whether the product launch is ready.",
        workspace_cwd=workspace,
        planner=lambda task: [
            "Evaluate pricing readiness",
            "Evaluate latency/SLA readiness",
            "Evaluate safety/compliance readiness",
        ],
        concurrency=2,  # at most two workers at once
    )

    print("workspace:", workspace)
    print("plan:")
    for i, subtask in enumerate(result.plan.subtasks, start=1):
        print(f"  {i}. {subtask}")
    print("artifacts:")
    for art in result.artifacts:
        print(f"  {art.path} ({art.char_count} chars) — {art.summary}")
    print("satisfied:", result.satisfied)
    print("final answer:", result.answer)
    return 0 if result.satisfied else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
