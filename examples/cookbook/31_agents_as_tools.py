"""Agents-as-tools and handoffs: decentralized delegation (offline).

Coordination C6. The complement to the supervisor track: expose a specialist agent as a
tool the lead's model can call. `agent_as_tool` (``ask_<agent>``) delegates a narrow
subtask and returns the answer to the caller; `handoff_tool` (``transfer_to_<agent>``)
hands the rest of the answer to the specialist. Both build on a C2 `AgentDefinition` (or a
`SubagentSpec`) plus `run_subagent` and register like any custom tool. Here we invoke the
handlers directly to show the delegation without needing a tool-calling model.

    python examples/cookbook/31_agents_as_tools.py
"""

from __future__ import annotations

import asyncio

from agent_driver.agents import AgentDefinition
from agent_driver.llm import FakeProvider
from agent_driver.sdk import (
    ToolRegistry,
    ToolSet,
    agent_as_tool,
    create_agent,
    handoff_tool,
)


async def main() -> int:
    lead = create_agent(
        provider=FakeProvider(response_text="specialist output"),
        tools=ToolSet.only(),
    )

    researcher = AgentDefinition(
        name="researcher",
        description="Finds and summarizes source material.",
        when_to_use="Use for open-ended fact-finding subtasks.",
        system_prompt="You are a meticulous researcher.",
    )
    ask_researcher = agent_as_tool(lead, researcher)
    transfer_to_writer = handoff_tool(lead, researcher, name="transfer_to_writer")

    # They register like any custom tool, so a tool-calling model can invoke them.
    registry = ToolRegistry()
    registry.register(ask_researcher.manifest, ask_researcher.handler)
    registry.register(transfer_to_writer.manifest, transfer_to_writer.handler)
    print("registered tools:", ask_researcher.manifest.name, "/", transfer_to_writer.manifest.name)

    # agent-as-tool: delegate a subtask, keep driving.
    delegated = await ask_researcher.handler({"input": "What are the top 3 competitors?"})
    print("ask_researcher →", {k: delegated[k] for k in ("agent", "status", "handoff")})

    # handoff: the specialist owns the rest of the answer.
    handed_off = await transfer_to_writer.handler({"input": "Write the final brief."})
    print("transfer_to_writer →", {k: handed_off[k] for k in ("status", "handoff")})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
