"""Agent registry: define specialized agents as Markdown, run them (offline).

Coordination C2. An agent *type* is data — a Markdown file with YAML frontmatter
(name, tools, model, budget) + a body that is the child's system prompt. Register
definitions (built-ins low priority, project files higher), resolve an `agent_type`
by name, bridge it to a `SubagentSpec`, and run it with `run_subagent`.

    python examples/cookbook/25_agent_registry.py
"""

from __future__ import annotations

import asyncio

from agent_driver.agents import (
    AgentRegistry,
    agent_definition_to_spec,
    parse_agent_markdown,
)
from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent, run_subagent

_EXPLORER = """---
name: explorer
description: Read-only investigator; gather facts before acting.
when_to_use: Use to look things up; never to modify state.
tools: [web_search]
model_role: simple
max_tool_calls: 20
---
You are a read-only explorer. Investigate and report concise findings. Never modify anything.
"""


async def main() -> str:
    registry = AgentRegistry()
    # A host can register built-ins at low priority, then let project files
    # (registry.register_directory('.agents', priority=10)) override by name.
    registry.register(parse_agent_markdown(_EXPLORER, source="builtin"), priority=0)
    print("registered agents:", registry.names())

    parent = create_agent(
        provider=FakeProvider(response_text="West total is 42."),
        tools=ToolSet.only("web_search"),
    )
    definition = registry.get("explorer")
    assert definition is not None
    spec = agent_definition_to_spec(definition, prompt="What is the West total?")
    result = await run_subagent(parent, spec)

    print(f"agent '{spec.agent_type}' (tools={spec.allowed_tools}) ->", result.answer)
    return result.answer or ""


if __name__ == "__main__":
    asyncio.run(main())
