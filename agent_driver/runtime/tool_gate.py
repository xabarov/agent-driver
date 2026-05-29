"""Dynamic per-call tool gate — caller-supplied approval/deny/ask hook.

Why this exists alongside ``ToolPolicyInput``
---------------------------------------------

``ToolPolicyInput`` is **static**: it can deny by tool name, deny by
``denied_tools``, or require approval based on the tool's manifest-level
risk. Those are decisions you can make from the **schema** alone —
before the model emits args.

There are decisions you can only make from the **args**:

* ``sandbox`` is read-only by manifest, but the model just emitted SQL
  whose ``code`` contains ``DELETE FROM ...``.
* ``excel_find`` is read-only, but the planned ``max_rows`` is
  ``10_000_000``.
* ``chart_render`` is allowed, but the planned target is an external S3
  bucket the operator hasn't whitelisted.

``ToolGate`` is the seam for these arg-level checks. It runs **after**
the static ``evaluate_tool_policy`` returns ALLOW and **before** the
guardrails / tool handler. The gate sees the full planned call shape
and returns one of three results:

* :class:`ToolGateAllow` — let the call through. No-op.
* :class:`ToolGateDeny` — convert to a blocked envelope (so the LLM
  sees the denial in its tool result trace and can react / abandon /
  re-plan).
* :class:`ToolGateAsk` — pause the run and emit an
  :class:`~agent_driver.contracts.interrupts.InterruptRequest` with
  ``reason="approval_required"``. The host's interrupt protocol then
  carries the request to the operator UI; on ``ResumeAction.APPROVE``
  the runtime resumes with the (possibly edited) args.

Layering note
-------------

The gate runs **after** the prompt-based override
(:func:`agent_driver.tools.executor.governed._match_run_approved_prompts`)
so that an operator's prior "approve all `git status` calls" prompt
short-circuits the gate the same way it short-circuits the static
INTERRUPT. The gate is the most expressive seam — keep it last so
cheaper checks (manifest risk, denylist, prompt categories) win first.

Threading + cancellation
------------------------

The gate is invoked with ``await``; it may suspend on DB I/O,
sub-query LLM classifiers, operator polling, etc. The
:class:`~agent_driver.runtime.abort.RunAbortHandle` of the enclosing
run is **not** propagated into the gate directly — the gate should
keep its own timeouts. If the gate raises, the runtime treats it as
``ToolGateDeny`` with the exception text as ``reason`` (fail-closed —
better to block one tool call than to silently bypass operator-level
risk checks).

See also
--------

* ``docs/runtime/tool_gate.md`` for the use-case rationale (TBD).
* ``agent_driver.tools.policy.evaluator.evaluate_tool_policy`` — the
  static schema-level pass that runs first.
* ``agent_driver.contracts.interrupts.InterruptRequest`` — the
  contract emitted on ``ToolGateAsk``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolGateAllow:
    """Approve the planned call. Identical to no gate at all.

    ``reason`` is optional and only used for telemetry — it does not
    appear in the LLM-visible tool trace.
    """

    reason: str | None = None
    decision: Literal["allow"] = "allow"


@dataclass(frozen=True, slots=True)
class ToolGateDeny:
    """Block the planned call.

    The runtime materialises this as a blocked envelope with
    ``code="tool_gate_denied"`` so the LLM sees the denial and can
    re-plan. Use this for **hard** policy violations the operator has
    pre-committed to (e.g. "never call ``sandbox`` with raw DELETE on
    a production schema") — the gate is fire-and-forget; nothing
    pauses, nothing prompts.
    """

    reason: str
    decision: Literal["deny"] = "deny"


@dataclass(frozen=True, slots=True)
class ToolGateAsk:
    """Pause the run and prompt the operator for approval.

    The runtime emits an :class:`InterruptRequest` with
    ``reason="approval_required"``, proposed_action carrying the full
    planned call, and ``allowed_actions=[APPROVE, REJECT, EDIT,
    CLARIFY, CANCEL]``. The host's interrupt protocol carries this to
    the operator UI; the standard resume path (``Agent.approve`` /
    ``edit`` / ``reject``) then re-enters the run.

    ``message`` is the operator-facing description ("This will delete
    47 rows. Approve?"). ``title`` overrides the default
    ``"Approval required for '<tool_name>'"`` heading.
    """

    message: str
    title: str | None = None
    decision: Literal["ask"] = "ask"


ToolGateResult = ToolGateAllow | ToolGateDeny | ToolGateAsk


@dataclass(frozen=True, slots=True)
class ToolGateContext:
    """Read-only view of the planned call that the gate consults.

    Fields are limited to what an arg-level decision actually needs.
    The gate intentionally cannot see the conversation history,
    other planned calls in the same batch, or run scratch state —
    keep gate decisions local and deterministic.

    Attributes:
        tool_name: name of the planned tool.
        args: the tool's planned arguments as the model emitted them
            (already JSON-decoded). The gate may inspect freely but
            must not mutate; the runtime uses the SAME dict downstream.
        run_id: the run's identifier; useful for cross-correlating with
            host-side audit logs.
        thread_id: the conversation/thread identifier when known.
        agent_id: the active agent profile identifier (e.g.
            ``"plan_mode"`` vs ``"react_text"``); lets the gate apply
            stricter policy to specific profiles.
        risk: the tool manifest's declared risk level
            (``"low" | "medium" | "high" | "critical"``).
        side_effect: the tool manifest's side-effect class
            (``"read" | "write" | "external"``).
        current_tool_calls: how many tool calls the run has made
            BEFORE this one. Useful for budget-style gates ("first 3
            free, then ask").
    """

    tool_name: str
    args: dict[str, Any]
    run_id: str | None
    thread_id: str | None
    agent_id: str
    risk: str
    side_effect: str
    current_tool_calls: int


ToolGate = Callable[[ToolGateContext], Awaitable[ToolGateResult]]
"""Type alias for a tool-gate function.

A ``ToolGate`` is any async callable that takes a
:class:`ToolGateContext` and returns one of :class:`ToolGateAllow`,
:class:`ToolGateDeny`, :class:`ToolGateAsk`.

Errors are caught by the runtime and treated as ``ToolGateDeny`` with
the exception message as reason — fail-closed by design.
"""


__all__ = [
    "ToolGate",
    "ToolGateAllow",
    "ToolGateAsk",
    "ToolGateContext",
    "ToolGateDeny",
    "ToolGateResult",
]
