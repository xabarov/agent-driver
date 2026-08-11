"""Markdown-defined agent types + registry (coordination C2).

A domain-neutral way to define reusable specialized agents as data (Markdown files
with YAML frontmatter, like Claude Code's ``.claude/agents`` and OpenHands'
subagent registry) and resolve an ``agent_type`` name to a runnable
:class:`~agent_driver.sdk.subagent.SubagentSpec`:

    from agent_driver.agents import AgentRegistry, agent_definition_to_spec
    from agent_driver.sdk import run_subagent

    registry = AgentRegistry()
    registry.register_directory(".agents", priority=10)  # project agents
    spec = agent_definition_to_spec(registry.get("explorer"), prompt="Find X")
    result = await run_subagent(parent, spec)
"""

from agent_driver.agents.definition import (
    AgentDefinition,
    load_agent_definitions,
    parse_agent_markdown,
)
from agent_driver.agents.registry import AgentRegistry
from agent_driver.agents.spec import agent_definition_to_spec

__all__ = [
    "AgentDefinition",
    "AgentRegistry",
    "agent_definition_to_spec",
    "load_agent_definitions",
    "parse_agent_markdown",
]
