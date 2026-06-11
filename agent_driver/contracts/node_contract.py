"""NodeContract — a contract-following envelope for workflow/harness runs.

An **opt-in** companion to :class:`AgentRunInput`. When a host app drives
agent-driver as a single workflow node — a fixed ``tool_policy.allowed_tools``
plus a concrete task+target in the prompt — the bare ReAct loop can drift into a
generic assistant reply ("What domain?", "I don't have tools") or keep the LLM
running long after tool evidence is already sufficient. ``NodeContract`` wraps
the loop in three opt-in layers:

* **A — policy↔registry validation** (``require_callable_tools``): at run start,
  any ``allowed_tools`` name that isn't actually callable in the registry is
  surfaced as a structured warning instead of being silently dropped.
* **B — tool-use contract** (``require_tool_use``): finalizing with zero tool
  calls is treated as a *recoverable* violation — the runtime reprompts with the
  concrete tools + target, then (if still unsatisfied) stamps a structured
  violation rather than returning a silent generic answer.
* **C — early finalize from tool evidence** (``finalize_when_tools`` and the
  ``on_tool_evidence`` lifecycle hook): once the declared tools have produced
  successful evidence, the run finalizes directly — no extra LLM continuation.

Absent (the default), behaviour is byte-for-byte unchanged. See
``docs/node-contract-plan-2026-06-11.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel


class NodeContract(ContractModel):
    """Opt-in contract-following config for a single workflow/harness node."""

    # --- Layer A: policy↔registry validation -------------------------------
    require_callable_tools: bool = False
    """When True, validate ``tool_policy.allowed_tools`` (and
    ``finalize_when_tools``) against the live tool registry at run start and emit
    a structured warning for any name that is not callable."""

    # --- Layer B: tool-use contract ----------------------------------------
    require_tool_use: bool = False
    """When True, a run that finalizes having made zero tool calls is a
    recoverable violation: the runtime reprompts (up to ``max_tool_use_reprompts``)
    with the concrete tools + target, then stamps a structured violation."""

    max_tool_use_reprompts: int = 1
    """How many times to reprompt a zero-tool-call finalize before escalating to a
    structured violation. ``0`` disables the reactive reprompt (the proactive
    prelude still applies)."""

    on_violation: Literal["reprompt_then_error"] = "reprompt_then_error"
    """Escalation policy for a tool-use violation. Currently a single mode:
    reprompt up to the bound, then finalize with a structured violation stamped on
    ``output.metadata`` (never a silent generic answer)."""

    target: str | None = None
    """Concrete target (e.g. a domain) woven into the proactive tool-use prelude
    so the model never asks "which target?" when it is already known."""

    task_hint: str | None = None
    """One-line task description for the proactive prelude (e.g. "enumerate passive
    subdomains")."""

    # --- Layer C: early finalize from tool evidence ------------------------
    finalize_when_tools: list[str] = Field(default_factory=list)
    """Once every listed tool has produced a successful (non-error) envelope, the
    run finalizes directly without another LLM continuation."""

    @field_validator("max_tool_use_reprompts")
    @classmethod
    def _validate_reprompts(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_tool_use_reprompts must be >= 0")
        return value

    def is_active(self) -> bool:
        """Whether any layer is engaged (cheap gate for the runtime fast-path)."""
        return bool(
            self.require_callable_tools
            or self.require_tool_use
            or self.finalize_when_tools
        )


@dataclass(frozen=True, slots=True)
class FinalizeNow:
    """An ``on_tool_evidence`` hook's directive to finalize from tool evidence.

    The runtime routes straight to ``finalize`` (skipping the next LLM pass) and
    uses ``answer`` as the terminal answer. ``reason`` is recorded on the run for
    diagnostics. Return ``None`` from the hook to let the loop continue normally.
    """

    answer: str
    reason: str = "tool_evidence_satisfies_contract"


__all__ = ["NodeContract", "FinalizeNow"]
