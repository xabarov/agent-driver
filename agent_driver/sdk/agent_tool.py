"""Agents-as-tools and handoffs (coordination C6).

The supervisor topology (C4/C5) is centralized: a lead fans work out and joins it back.
The complementary pattern, from the OpenAI Agents SDK, is *decentralized* delegation —
expose a specialist agent as a tool the lead's model can call:

- **agent-as-tool** (`agent_as_tool`, default name ``ask_<agent>``) — the specialist runs a
  narrow subtask and returns its answer to the caller, who keeps driving. A scoped
  delegation, like any other tool call.
- **handoff** (`handoff_tool`, default name ``transfer_to_<agent>``) — the specialist owns
  the *rest* of the answer: the caller is instructed to relay the specialist's answer as its
  final answer rather than re-deriving it. A cooperative control transfer (the specialist
  finishes the substance), not a runtime driver swap — kept at the SDK layer to stay
  domain-neutral.

Both are built on the C2 registry (an `AgentDefinition`) or a hand-built `SubagentSpec` plus
`run_subagent`: the spec is a reusable template, and each call rebinds the model-supplied
input as the child's prompt. This is opt-in — the supervisor track is the safer 2026 default
and less prone to the MAST circular-handoff failure mode — so handoffs are a tool you add
deliberately, not a default topology.
"""

from __future__ import annotations

import re
from typing import Any

from agent_driver.agents.definition import AgentDefinition
from agent_driver.agents.spec import agent_definition_to_spec
from agent_driver.contracts.tools.manifest import ToolManifest
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent
from agent_driver.sdk.subagent import SubagentResult, SubagentSpec, run_subagent
from agent_driver.tools.custom import CustomToolDefinition

_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _slug(name: str) -> str:
    return _UNSAFE.sub("_", name).strip("_") or "agent"


def _template_agent_type(spec: SubagentSpec | AgentDefinition) -> str:
    return spec.name if isinstance(spec, AgentDefinition) else spec.agent_type


def _spec_for(spec: SubagentSpec | AgentDefinition, prompt: str) -> SubagentSpec:
    if isinstance(spec, AgentDefinition):
        return agent_definition_to_spec(spec, prompt=prompt)
    if isinstance(spec, SubagentSpec):
        return spec.with_prompt(prompt)
    raise TypeError("spec must be a SubagentSpec or an AgentDefinition")


def _build_agent_tool(
    parent: Agent,
    spec: SubagentSpec | AgentDefinition,
    *,
    name: str,
    description: str,
    input_arg: str,
    input_description: str,
    handoff: bool,
    tool_gate: ToolGate | None,
    parent_run_id: str | None,
    parent_abort_handle: RunAbortHandle | None,
) -> CustomToolDefinition:
    agent_type = _template_agent_type(spec)

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get(input_arg) or "").strip()
        result: SubagentResult = await run_subagent(
            parent,
            _spec_for(spec, prompt),
            parent_run_id=parent_run_id,
            parent_abort_handle=parent_abort_handle,
            tool_gate=tool_gate,
        )
        return {
            "agent": agent_type,
            "status": result.status.value,
            "answer": result.answer or "",
            "handoff": handoff,
        }

    manifest = ToolManifest(
        name=name,
        description=description,
        idempotent=False,  # running a specialist is not a pure read
        args_schema={
            "type": "object",
            "properties": {
                input_arg: {"type": "string", "description": input_description}
            },
            "required": [input_arg],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={"agent_tool": agent_type, "handoff": handoff},
    )
    return CustomToolDefinition(manifest=manifest, handler=_handler)


def agent_as_tool(
    parent: Agent,
    spec: SubagentSpec | AgentDefinition,
    *,
    name: str | None = None,
    description: str | None = None,
    input_arg: str = "input",
    input_description: str | None = None,
    tool_gate: ToolGate | None = None,
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
) -> CustomToolDefinition:
    """Expose a specialist agent as a tool that runs a narrow subtask and returns its answer.

    ``spec`` is the reusable template (an `AgentDefinition` from the C2 registry or a
    hand-built `SubagentSpec`); each call rebinds the model-supplied ``input`` as the child's
    prompt and runs it via `run_subagent`, returning ``{agent, status, answer, handoff}``.
    The default tool name is ``ask_<agent>``. The caller keeps driving after the tool returns.
    """
    agent_type = _template_agent_type(spec)
    when = spec.when_to_use if isinstance(spec, AgentDefinition) else None
    desc = spec.description if isinstance(spec, AgentDefinition) else None
    return _build_agent_tool(
        parent,
        spec,
        name=name or f"ask_{_slug(agent_type)}",
        description=(
            description
            or (f"Delegate a subtask to the {agent_type} specialist. " + (when or desc or "")).strip()
        ),
        input_arg=input_arg,
        input_description=input_description or f"The subtask for the {agent_type} specialist.",
        handoff=False,
        tool_gate=tool_gate,
        parent_run_id=parent_run_id,
        parent_abort_handle=parent_abort_handle,
    )


def handoff_tool(
    parent: Agent,
    spec: SubagentSpec | AgentDefinition,
    *,
    name: str | None = None,
    description: str | None = None,
    input_arg: str = "input",
    input_description: str | None = None,
    tool_gate: ToolGate | None = None,
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
) -> CustomToolDefinition:
    """Expose a specialist as a handoff — it owns the rest of the answer.

    Like :func:`agent_as_tool`, but the default name is ``transfer_to_<agent>`` and the
    description instructs the caller to relay the specialist's answer as its *final* answer
    rather than re-deriving it (a cooperative control transfer). Use when the chosen
    specialist should finish the turn's substance; prefer the supervisor track when the lead
    must still synthesize across several workers (handoffs are more MAST circular-delegation
    prone).
    """
    agent_type = _template_agent_type(spec)
    when = spec.when_to_use if isinstance(spec, AgentDefinition) else None
    return _build_agent_tool(
        parent,
        spec,
        name=name or f"transfer_to_{_slug(agent_type)}",
        description=(
            description
            or (
                f"Hand off to the {agent_type} specialist, which owns the rest of the "
                f"answer. Relay its answer as your final answer. " + (when or "")
            ).strip()
        ),
        input_arg=input_arg,
        input_description=input_description
        or f"The full task to hand off to the {agent_type} specialist.",
        handoff=True,
        tool_gate=tool_gate,
        parent_run_id=parent_run_id,
        parent_abort_handle=parent_abort_handle,
    )


__all__ = ["agent_as_tool", "handoff_tool"]
