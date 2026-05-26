"""Phase 11 H15 — pre/post tool-use hook contracts.

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
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope


@runtime_checkable
class ToolHook(Protocol):
    """Optional pre/post hook applied to one tool call.

    ``name`` is used for deduplicated error reporting; pick something
    short and stable (e.g. ``"secret_redactor"``).

    Hooks declare both ``pre_tool_use`` and ``post_tool_use``; either
    may be a no-op (return ``None``). Returning a new ``ToolCall`` /
    ``ToolResultEnvelope`` replaces the value in the runtime chain.
    Returning ``None`` means "no change — pass through to the next hook
    or to the executor".
    """

    name: str

    async def pre_tool_use(
        self, call: ToolCall, context: dict[str, Any]
    ) -> ToolCall | None:
        """Inspect / transform a tool call before policy + guardrails."""

    async def post_tool_use(
        self, envelope: ToolResultEnvelope, context: dict[str, Any]
    ) -> ToolResultEnvelope | None:
        """Inspect / transform a tool result envelope before persisting."""


class BaseToolHook:
    """Convenience base class with no-op implementations.

    Subclass and override only the side you care about; the other
    method returns ``None`` (no change).
    """

    name: str = "base_tool_hook"

    async def pre_tool_use(
        self, call: ToolCall, context: dict[str, Any]
    ) -> ToolCall | None:  # pragma: no cover - default no-op
        return None

    async def post_tool_use(
        self, envelope: ToolResultEnvelope, context: dict[str, Any]
    ) -> ToolResultEnvelope | None:  # pragma: no cover - default no-op
        return None


__all__ = ["BaseToolHook", "ToolHook"]
