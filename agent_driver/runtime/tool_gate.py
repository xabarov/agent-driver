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

from agent_driver.contracts.validation import (
    RESERVED_METADATA_PREFIX,
    ensure_bounded_json_metadata,
)

# Reserved key under which the runtime stores validated gate provenance on a
# tool policy outcome (and downstream projections). Lives inside the reserved
# ``_ad_`` namespace so model- or tool-authored metadata can neither forge nor
# overwrite it — host metadata carrying an ``_ad_`` key is rejected upstream.
RESERVED_GATE_PROVENANCE_KEY = f"{RESERVED_METADATA_PREFIX}gate_provenance"


@dataclass(frozen=True, slots=True)
class GateProvenance:
    """Opaque, host-authored provenance a gate attaches to its decision.

    The runtime preserves these fields through the tool policy outcome and
    (for the ask path) the approval interrupt the host resumes against, under
    the reserved :data:`RESERVED_GATE_PROVENANCE_KEY` namespace. All fields are
    optional; ``metadata`` must be JSON-safe and bounded
    (:func:`ensure_bounded_json_metadata`) and must not itself use the reserved
    ``_ad_`` namespace, or the runtime fails the call closed (treats it as a
    :class:`ToolGateDeny`).

    Attributes:
        decision_id: opaque host decision identifier (e.g. the id of the
            external policy evaluation that produced this gate result).
        policy_snapshot_id: opaque id of the host policy snapshot the decision
            was evaluated against — lets the host bind the decision to a
            specific version of its own policy.
        metadata: free-form JSON-safe host metadata, bounded and reserved-key
            clean.
    """

    decision_id: str | None = None
    policy_snapshot_id: str | None = None
    metadata: dict[str, Any] | None = None


def build_gate_provenance_metadata(provenance: GateProvenance) -> dict[str, Any]:
    """Validate + pack host gate provenance under the reserved namespace key.

    Raises ``ValueError`` on non-JSON / oversized / too-deep / reserved-key
    host metadata so the caller can fail the gated call closed. Returns a dict
    ``{RESERVED_GATE_PROVENANCE_KEY: {...}}`` ready to merge into a policy
    outcome's ``metadata``.
    """
    host_meta = provenance.metadata
    if host_meta is not None:
        # Host metadata must be bounded AND may not use the reserved namespace
        # (that is the authorship isolation guarantee).
        ensure_bounded_json_metadata(host_meta, field_name="gate provenance metadata")
    packed = {
        "decision_id": provenance.decision_id,
        "policy_snapshot_id": provenance.policy_snapshot_id,
        "metadata": host_meta,
    }
    # Validate the packed record too; allow the reserved wrapper key itself.
    ensure_bounded_json_metadata(
        {RESERVED_GATE_PROVENANCE_KEY: packed},
        field_name="gate provenance",
        allow_reserved_keys=True,
    )
    return {RESERVED_GATE_PROVENANCE_KEY: packed}


def extract_reserved_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return only the reserved (``_ad_``) keys of a metadata dict.

    Used to forward runtime-authored provenance/identity through a projection
    (e.g. the approval interrupt) without dragging along host run-metadata that
    already flows by another path.
    """
    if not metadata:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if isinstance(key, str) and key.startswith(RESERVED_METADATA_PREFIX)
    }


@dataclass(frozen=True, slots=True)
class ToolGateAllow:
    """Approve the planned call. Identical to no gate at all.

    ``reason`` is optional and only used for telemetry — it does not
    appear in the LLM-visible tool trace. ``provenance`` (optional) is
    preserved on the policy outcome so a host can audit *why* the call was
    allowed.
    """

    reason: str | None = None
    provenance: GateProvenance | None = None
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
    provenance: GateProvenance | None = None
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
    provenance: GateProvenance | None = None
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
        tool_call_id: stable identifier of the logical planned call, so a
            host can bind its external policy decision to the exact call that
            survives gate -> interrupt -> approval/resume. May be ``None`` when
            the provider did not supply one (a positional fallback is used).
        attempt_id: identifier of this execution attempt of the call; distinct
            per retry so a host can tell a re-evaluation from the first pass.
    """

    tool_name: str
    args: dict[str, Any]
    run_id: str | None
    thread_id: str | None
    agent_id: str
    risk: str
    side_effect: str
    current_tool_calls: int
    tool_call_id: str | None = None
    attempt_id: str | None = None


ToolGate = Callable[[ToolGateContext], Awaitable[ToolGateResult]]
"""Type alias for a tool-gate function.

A ``ToolGate`` is any async callable that takes a
:class:`ToolGateContext` and returns one of :class:`ToolGateAllow`,
:class:`ToolGateDeny`, :class:`ToolGateAsk`.

Errors are caught by the runtime and treated as ``ToolGateDeny`` with
the exception message as reason — fail-closed by design.
"""


__all__ = [
    "RESERVED_GATE_PROVENANCE_KEY",
    "GateProvenance",
    "ToolGate",
    "ToolGateAllow",
    "ToolGateAsk",
    "ToolGateContext",
    "ToolGateDeny",
    "ToolGateResult",
    "build_gate_provenance_metadata",
    "extract_reserved_metadata",
]
