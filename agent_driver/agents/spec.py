"""Bridge an :class:`AgentDefinition` to a runnable ``SubagentSpec`` (C2)."""

from __future__ import annotations

from agent_driver.agents.definition import AgentDefinition
from agent_driver.sdk.subagent import SubagentSpec


def agent_definition_to_spec(
    definition: AgentDefinition, *, prompt: str, agent_type: str | None = None
) -> SubagentSpec:
    """Build a ``SubagentSpec`` for one task from a registry ``definition``.

    The definition supplies the reusable shape (system prompt, tool allow/deny,
    model/effort, budget); ``prompt`` is the per-invocation task. ``agent_type``
    overrides the trace label (defaults to the definition name). The result runs
    through :func:`agent_driver.sdk.run_subagent` like any hand-built spec.
    """
    return SubagentSpec(
        agent_type=agent_type or definition.name,
        prompt=prompt,
        system_prompt=definition.system_prompt or None,
        allowed_tools=definition.allowed_tools,
        denied_tools=definition.denied_tools,
        model=definition.model,
        model_role=definition.model_role,
        reasoning_effort=definition.reasoning_effort,
        max_tool_calls=definition.max_tool_calls,
        deadline_seconds=definition.deadline_seconds,
        max_cost_usd=definition.max_cost_usd,
        app_metadata=dict(definition.metadata) or None,
    )


__all__ = ["agent_definition_to_spec"]
