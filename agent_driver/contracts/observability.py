"""Observability contract: observer/middleware split + schema versions (epic 037).

Our lifecycle-hook plane (epic 010) is typed but does not formally distinguish
**observers** (read-only, fail-open — report what happened) from **middleware**
(behavior-changing — rewrite a request or wrap execution). Reference-first
(hermes ``docs/observability`` + ``docs/middleware``): the two are separate
contracts with separate version strings, and observer payloads are bounded +
secret-redacted before they leave the process. This module formalizes the split
for the EXISTING :class:`~agent_driver.runtime.lifecycle_hooks.RunLifecycleHook`
methods — no behavior change, just a machine-checkable classification + versioned
contract other layers (trace assembly, host redaction) can key on.

The classification is locked by a test: every Protocol method must appear in
exactly one of the two sets, so adding a hook method is a conscious observer-vs-
middleware decision rather than silent drift.
"""

from __future__ import annotations

from hashlib import sha1
from typing import Any

# Version strings mirror hermes ``hermes.observer.v1`` / ``hermes.middleware.v1``.
# Stamped onto emitted correlation so a subscriber can pin the contract it parses.
OBSERVER_SCHEMA_VERSION = "agent_driver.observer.v1"
MIDDLEWARE_SCHEMA_VERSION = "agent_driver.middleware.v1"

# Observer methods: read-only, return ``None``, MUST NOT change run behaviour.
# A raising observer is isolated and logged (fail-open) — never propagated.
OBSERVER_HOOK_METHODS = frozenset(
    {
        "on_run_start",
        "after_llm_response",
        "on_error",
        "on_run_completed",
    }
)

# Middleware methods: behavior-changing. Their return value alters the run —
# ``before_llm_request`` replaces the request, ``on_finalize`` requests a
# revision, ``on_tool_evidence`` finalizes early from tool evidence.
MIDDLEWARE_HOOK_METHODS = frozenset(
    {
        "before_llm_request",
        "on_finalize",
        "on_tool_evidence",
    }
)

# The full set of classified hook methods — kept in sync with the Protocol by
# ``tests/contracts/test_observability_contract.py`` (both directions).
CLASSIFIED_HOOK_METHODS = OBSERVER_HOOK_METHODS | MIDDLEWARE_HOOK_METHODS


def hook_method_role(name: str) -> str | None:
    """Return ``"observer"`` / ``"middleware"`` for a hook method, else ``None``."""
    if name in OBSERVER_HOOK_METHODS:
        return "observer"
    if name in MIDDLEWARE_HOOK_METHODS:
        return "middleware"
    return None


def deterministic_trace_id(run_id: str, attempt_id: str) -> str:
    """Stable trace id for a (run, attempt) pair — the single correlation seed.

    Epic 037 phase B: span↔run matching must NOT depend on wall-clock / timestamp
    proximity (the Phoenix container has observed clock skew). Both the live emit
    path (every :class:`~agent_driver.contracts.events.RuntimeEvent`) and the
    deterministic trace export derive the trace id from ``run_id:attempt_id`` via
    this one function, so an event and its span share an id by construction. Must
    stay byte-identical to the historical ``trace_builder`` formula.
    """
    seed = f"{run_id}:{attempt_id}"
    return f"trace_{sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def correlation_ids(
    run_id: str, attempt_id: str, *, thread_id: str | None = None
) -> dict[str, str]:
    """Canonical correlation-id bundle for a run (reference: hermes ID table).

    ``trace_id`` is the deterministic derivation, so callers stamping this onto a
    payload get the same id the trace carries. ``thread_id`` (conversation) is
    included only when present.
    """
    ids = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "trace_id": deterministic_trace_id(run_id, attempt_id),
    }
    if thread_id:
        ids["thread_id"] = thread_id
    return ids


def describe_observability_contract() -> dict[str, Any]:
    """Machine-readable contract descriptor (versions + method classification).

    Suitable for stamping onto a trace/support bundle so a consumer can pin the
    observer/middleware schema it is parsing without importing engine internals.
    """
    return {
        "observer_schema_version": OBSERVER_SCHEMA_VERSION,
        "middleware_schema_version": MIDDLEWARE_SCHEMA_VERSION,
        "observer_methods": sorted(OBSERVER_HOOK_METHODS),
        "middleware_methods": sorted(MIDDLEWARE_HOOK_METHODS),
    }


__all__ = [
    "OBSERVER_SCHEMA_VERSION",
    "MIDDLEWARE_SCHEMA_VERSION",
    "OBSERVER_HOOK_METHODS",
    "MIDDLEWARE_HOOK_METHODS",
    "CLASSIFIED_HOOK_METHODS",
    "hook_method_role",
    "deterministic_trace_id",
    "correlation_ids",
    "describe_observability_contract",
]
