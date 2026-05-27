"""Phase 11 H15 + Phase 12 H22 — pre/post tool-use hook contracts.

Hooks live next to guardrails but are semantically distinct: guardrails
make a *decide-only* judgement (allow / sanitize / block) on a fixed
call/result; hooks may **transform** the call input or result envelope
before the runtime accepts it.

Typical uses:

* secret redaction in pre-hook (strip ``sk-`` prefixes from
  ``shell.command`` args);
* observability enrichment in post-hook (add ``app_trace_id``,
  per-tool latency annotations to ``envelope.metadata``);
* test fixtures (deterministic-replay shim that returns canned
  envelopes for a specific tool name).

Hooks DO NOT replace guardrails — guardrails still run on the
hook-modified value. So a malicious pre-hook cannot bypass policy by
rewriting an unsafe call into a safe one's shape; the guardrails see
the transformed value.

Multiple hooks run in **registration order**. Each sees the previous
hook's output (chain). When a hook raises, the runtime logs a
deduplicated warning and falls back to the value BEFORE that hook ran
— the chain continues with the next hook.

Phase 12 H22 (added) — hooks may return either the raw replacement
value (``ToolCall`` / ``ToolResultEnvelope``) for backwards
compatibility OR a :class:`HookResponse` envelope that carries
additional aggregation hints:

* ``prevent_continuation`` — when True, the chain stops after this
  hook (subsequent hooks for the same event are skipped). Useful for
  security overlays that want to short-circuit on a deny decision
  without letting downstream hooks reopen the gate.
* ``additional_context`` — free-form dict merged into the next hook's
  ``context`` argument (and surfaced to the executor as metadata).
  Lets one hook annotate the call for inspection by later hooks
  without abusing the call args themselves.

Per-hook async timeouts: any hook may declare a ``timeout_seconds``
class attr (or instance attr). The chain runner awaits with that
budget; on timeout the hook is treated like a raised exception
(WARNING log, value preserved, chain continues with next hook).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class HookResponse(Generic[T]):
    """Phase 12 H22 — rich response envelope for hooks that need
    aggregation hints beyond a simple value replacement.

    A hook may return either:

    * ``None`` — no change to the chain value;
    * a raw ``T`` (``ToolCall`` or ``ToolResultEnvelope``) — replace
      the chain value; this is the H15 backwards-compat shape;
    * a ``HookResponse[T]`` — replace the chain value AND carry
      aggregation hints.

    Fields:

    * ``value`` — the replacement chain value, or ``None`` to leave
      it unchanged (combine with ``prevent_continuation`` or
      ``additional_context`` to signal stuff without modifying the
      value).
    * ``prevent_continuation`` — when True, the chain exits after this
      hook. Subsequent hooks for the same event are skipped. The
      executor still uses the current chain value as the final
      result.
    * ``additional_context`` — dict merged into the next hook's
      ``context`` argument (shallow merge — hook keys take
      precedence). Also surfaced into ``envelope.metadata`` under
      ``hook_context_<hook_name>`` after post-hook aggregation.
    """

    value: T | None = None
    prevent_continuation: bool = False
    additional_context: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ToolHook(Protocol):
    """Optional pre/post hook applied to one tool call.

    ``name`` is used for deduplicated error reporting; pick something
    short and stable (e.g. ``"secret_redactor"``).

    Hooks declare both ``pre_tool_use`` and ``post_tool_use``; either
    may be a no-op (return ``None``). Returning a new ``ToolCall`` /
    ``ToolResultEnvelope`` (or a :class:`HookResponse` wrapping one)
    replaces the value in the runtime chain. Returning ``None`` means
    "no change — pass through to the next hook or to the executor".

    Phase 12 H22 — optional ``timeout_seconds`` class/instance attr
    bounds how long the chain waits for each hook method. Default
    ``None`` (no timeout) preserves H15 behaviour.
    """

    name: str

    async def pre_tool_use(
        self, call: ToolCall, context: dict[str, Any]
    ) -> "ToolCall | HookResponse[ToolCall] | None":
        """Inspect / transform a tool call before policy + guardrails."""

    async def post_tool_use(
        self, envelope: ToolResultEnvelope, context: dict[str, Any]
    ) -> "ToolResultEnvelope | HookResponse[ToolResultEnvelope] | None":
        """Inspect / transform a tool result envelope before persisting."""


class BaseToolHook:
    """Convenience base class with no-op implementations.

    Subclass and override only the side you care about; the other
    method returns ``None`` (no change).

    Override ``timeout_seconds`` (class attr) to bound each hook
    method's wall-clock budget (Phase 12 H22).
    """

    name: str = "base_tool_hook"
    timeout_seconds: float | None = None

    async def pre_tool_use(
        self, call: ToolCall, context: dict[str, Any]
    ) -> "ToolCall | HookResponse[ToolCall] | None":  # pragma: no cover - default no-op
        return None

    async def post_tool_use(
        self, envelope: ToolResultEnvelope, context: dict[str, Any]
    ) -> "ToolResultEnvelope | HookResponse[ToolResultEnvelope] | None":  # pragma: no cover - default no-op
        return None


__all__ = ["BaseToolHook", "HookResponse", "ToolHook"]
