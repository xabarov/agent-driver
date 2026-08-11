"""See what a coordination run actually did — describe() (offline).

Coordination observability. A fan-out returns rich result objects, but answering "what did
each worker do, and why did one come back empty?" used to mean hand-walking nested
tool_trace / status / terminal_reason fields. `describe(result)` renders any coordination
result — a SubagentGroupResult, CoordinatorResult, or DeepAgentResult — as a compact,
human-readable trace that flags the usual failure modes (empty answer, non-completed
terminal reason, failed/denied tool call).

    python examples/cookbook/32_coordination_trace.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import (
    CoordinatorPhase,
    SubagentSpec,
    ToolSet,
    create_agent,
    describe,
    run_coordinator,
    run_subagent_group,
)


async def main() -> int:
    parent = create_agent(
        provider=FakeProvider(response_text="worker output"),
        tools=ToolSet.only(),
    )

    # A plain fan-out — describe() lists every child, its status, tools, and answer size.
    group = await run_subagent_group(
        parent, [SubagentSpec(agent_type=f"worker_{i}", prompt=f"part {i}") for i in range(3)]
    )
    print("=== group ===")
    print(describe(group))

    # A two-phase coordinator — describe() breaks it down per phase.
    phases = [
        CoordinatorPhase("research", lambda prior: [SubagentSpec(agent_type="researcher", prompt="dig")]),
        CoordinatorPhase("write", lambda prior: [SubagentSpec(agent_type="writer", prompt="compose")]),
    ]
    result = await run_coordinator(parent, phases)
    print("\n=== coordinator ===")
    print(describe(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
