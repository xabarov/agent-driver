"""``fork_subagent`` — system-prompt-cached child spawn.

Why this exists separately from ``run_subagent``
------------------------------------------------

``run_subagent`` is the general-purpose primitive: spawn a child with
arbitrary spec, no assumption about the parent's prompt. ``fork_subagent``
is a thin wrapper that adds **one specific guarantee**: the child's
system prompt is byte-identical to the parent's rendered system prompt.

This is not a cosmetic preference — it's required for prompt cache reuse
on the wire. Anthropic and OpenAI both cache request prefixes (system +
tools + first N messages). When the parent has already been seen by the
cache, a fork whose prefix matches exactly hits cache_read pricing
instead of input pricing — roughly 10× cheaper, faster start. Diverging
even one whitespace character invalidates the cache; this helper exists
so callers don't have to reconstruct the parent's prompt by hand.

What's NOT in this helper
-------------------------

* **No conversation history inheritance.** OpenClaude's
  ``forkContextMessages`` pattern injects specific parent messages into
  the child — that's a richer feature that requires reasoning about
  which turns are relevant. Out of scope for B0.2; landing it later
  means just extending this signature.
* **No cost auto-aggregation.** Per-subagent cost surfacing is B2.1.
* **No background / parallel execution.** Sync await like ``run_subagent``.

The fork helper is intentionally tiny: ~20 lines of glue plus a few
lines of docs. It's a code-smell barometer — if it grows we should
probably refactor into a richer ``fork_subagent_with(...)`` and keep
this one simple.
"""

from __future__ import annotations

from dataclasses import replace

from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent
from agent_driver.sdk.subagent import SubagentResult, SubagentSpec, run_subagent


async def fork_subagent(
    parent: Agent,
    parent_system_prompt: str,
    spec: SubagentSpec,
    *,
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
    tool_gate: ToolGate | None = None,
) -> SubagentResult:
    """Spawn a child whose system prompt is byte-identical to the parent's.

    The caller is responsible for handing in the parent's rendered
    system prompt (``parent_system_prompt``). For ``Agent`` instances
    that already track a stable system prompt on construction, this is
    a trivial getattr; for code-agent / runtime-composed prompts, the
    caller should snapshot whatever the parent's LLM call would see at
    the moment of fork.

    The child's spec ``system_prompt`` is overridden (a warning is
    NOT issued — the contract is "fork wins"; callers who want to
    layer extra instructions should bake them into the prompt
    themselves).

    Cache reuse is a wire-level concern; this helper only guarantees
    payload equality. Whether the provider returns ``cache_read``
    tokens depends on the provider's caching policy and whether the
    parent's request has been seen recently. Observability for cache
    hits surfaces on ``LlmResponse.usage`` when the provider populates
    it (see ``cached_input_tokens`` in the OpenAI-compat adapter).
    """
    forked_spec = replace(spec, system_prompt=parent_system_prompt)
    return await run_subagent(
        parent,
        forked_spec,
        parent_run_id=parent_run_id,
        parent_abort_handle=parent_abort_handle,
        tool_gate=tool_gate,
    )


__all__ = ["fork_subagent"]
