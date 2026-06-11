"""Run/turn lifecycle hooks for the single-agent runtime.

A small extensibility seam so cross-cutting capabilities (long-term memory,
scheduling, auditing, telemetry) can observe run boundaries without editing
the step loop. ``SingleAgentStepMixin`` dispatches these at run start and at
terminal finalize; hooks are awaited in registration order.

Hooks operate on the live :class:`RunContext`, so they can read run input and
read/write runtime state (preferably through a typed ``_MetadataView`` owner).
This module lives in the runtime layer, unlike the tool-level
:class:`~agent_driver.contracts.hooks.ToolHook`, because lifecycle hooks are
coupled to runtime state rather than to provider-neutral contracts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_driver.contracts.events import RuntimeEvent
    from agent_driver.contracts.node_contract import FinalizeNow
    from agent_driver.contracts.runtime import AgentRunOutput
    from agent_driver.contracts.tools.results import ToolResultEnvelope
    from agent_driver.llm.contracts import LlmRequest, LlmResponse
    from agent_driver.runtime.single_agent.types import RunContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RevisionRequest:
    """An ``on_finalize`` hook's request to revise instead of finishing.

    The runtime injects ``feedback`` as a user turn and resumes the run (bounded
    by a hard cap), letting a goal-gate / rubric drive iteration toward criteria.
    """

    feedback: str


@runtime_checkable
class RunLifecycleHook(Protocol):
    """Observer of run boundaries. Any method may be a no-op."""

    name: str

    async def on_run_start(self, context: "RunContext") -> None:
        """Called once when a run begins (before the first LLM call)."""

    async def before_llm_request(
        self, context: "RunContext", request: "LlmRequest"
    ) -> "LlmRequest | None":
        """Called before every provider call with the finalized request.

        Return a replacement request to transform it (inject prompt, filter
        tools, evict messages); return ``None`` to leave it unchanged. Hooks
        chain — each sees the prior hook's result.
        """

    async def after_llm_response(
        self, context: "RunContext", response: "LlmResponse"
    ) -> None:
        """Called after every provider call with the model's response."""

    async def on_finalize(
        self, context: "RunContext", *, answer: str
    ) -> "RevisionRequest | None":
        """Called when a run reaches its terminal final answer.

        Return a :class:`RevisionRequest` to send the run back for another
        attempt (a goal-gate / rubric not yet satisfied); return ``None`` to
        accept the answer and finish.
        """

    async def on_tool_evidence(
        self,
        context: "RunContext",
        envelopes: "list[ToolResultEnvelope]",
    ) -> "FinalizeNow | None":
        """Called after a tool stage that would otherwise loop back to the LLM.

        ``envelopes`` are the tool results produced this turn. Return a
        :class:`~agent_driver.contracts.node_contract.FinalizeNow` to finalize the
        run *now* from tool evidence — the runtime skips the next LLM pass and uses
        the directive's ``answer`` as the terminal answer. Return ``None`` to let
        the loop continue normally (the default). This is the host escape hatch for
        ``stop_after_tool_evidence`` / ``finalize_when_tools_satisfy_contract``.
        """

    async def on_error(
        self,
        context: "RunContext",
        *,
        output: "AgentRunOutput",
        events: "list[RuntimeEvent]",
    ) -> None:
        """Called once when a run terminates in failure / timeout.

        ``events`` is the run's emitted event log, so a hook can react to the
        specific tool failures and the terminal ``RUN_FAILED`` (e.g. a hook
        chain spawning a fallback). Not called for user-cancelled runs.
        """


class BaseRunLifecycleHook:
    """Convenience base with no-op implementations; override what you need."""

    name: str = "base_run_lifecycle_hook"

    async def on_run_start(self, context: "RunContext") -> None:
        """No-op run-start hook; override to react to run start."""

    async def before_llm_request(
        self, context: "RunContext", request: "LlmRequest"
    ) -> "LlmRequest | None":
        """No-op pre-request hook; override to transform the request."""

    async def after_llm_response(
        self, context: "RunContext", response: "LlmResponse"
    ) -> None:
        """No-op post-response hook; override to observe the response."""

    async def on_finalize(
        self, context: "RunContext", *, answer: str
    ) -> "RevisionRequest | None":
        """No-op finalize hook; override to accept or request a revision."""

    async def on_tool_evidence(
        self,
        context: "RunContext",
        envelopes: "list[ToolResultEnvelope]",
    ) -> "FinalizeNow | None":
        """No-op tool-evidence hook; override to finalize from tool evidence."""

    async def on_error(
        self,
        context: "RunContext",
        *,
        output: "AgentRunOutput",
        events: "list[RuntimeEvent]",
    ) -> None:
        """No-op error hook; override to react to a failed run."""


def _hook_name(hook: RunLifecycleHook) -> str:
    """Best-effort display name for diagnostics."""
    return getattr(hook, "name", None) or type(hook).__name__


async def dispatch_run_start(
    hooks: Iterable[RunLifecycleHook], context: "RunContext"
) -> None:
    """Invoke ``on_run_start`` for each hook; isolate per-hook failures."""
    for hook in hooks:
        try:
            await hook.on_run_start(context)
        except Exception:  # pylint: disable=broad-exception-caught
            # One failing observer must not abort the run or block other hooks.
            logger.exception(
                "lifecycle on_run_start failed for hook %r", _hook_name(hook)
            )


async def dispatch_finalize(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", *, answer: str
) -> "RevisionRequest | None":
    """Invoke ``on_finalize`` for each hook; return the first revision request.

    A hook that raises is logged and skipped (treated as "no revision"), so a
    faulty goal-gate cannot wedge the run at finalize.
    """
    revision: RevisionRequest | None = None
    for hook in hooks:
        try:
            result = await hook.on_finalize(context, answer=answer)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle on_finalize failed for hook %r", _hook_name(hook)
            )
            continue
        if result is not None and revision is None:
            revision = result
    return revision


async def dispatch_tool_evidence(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    envelopes: "list[ToolResultEnvelope]",
) -> "FinalizeNow | None":
    """Invoke ``on_tool_evidence`` for each hook; return the first finalize directive.

    A hook that raises is logged and skipped (treated as "continue"), so a faulty
    early-finalize hook degrades to normal looping rather than wedging the run.
    """
    directive: "FinalizeNow | None" = None
    for hook in hooks:
        try:
            result = await hook.on_tool_evidence(context, envelopes)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle on_tool_evidence failed for hook %r", _hook_name(hook)
            )
            continue
        if result is not None and directive is None:
            directive = result
    return directive


async def dispatch_error(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    *,
    output: "AgentRunOutput",
    events: "list[RuntimeEvent]",
) -> None:
    """Invoke ``on_error`` for each hook; isolate per-hook failures."""
    for hook in hooks:
        try:
            await hook.on_error(context, output=output, events=events)
        except Exception:  # pylint: disable=broad-exception-caught
            # Already on the error path — a failing error hook must not mask it.
            logger.exception("lifecycle on_error failed for hook %r", _hook_name(hook))


async def dispatch_before_llm(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", request: Any
) -> Any:
    """Chain ``before_llm_request`` hooks; return the (possibly transformed) request.

    A hook that raises is logged and skipped, leaving the request as produced by
    the prior hooks — a faulty transform degrades to a no-op rather than failing
    the LLM call.
    """
    for hook in hooks:
        try:
            replacement = await hook.before_llm_request(context, request)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle before_llm_request failed for hook %r", _hook_name(hook)
            )
            continue
        if replacement is not None:
            request = replacement
    return request


async def dispatch_after_llm(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", response: Any
) -> None:
    """Invoke ``after_llm_response`` for each hook; isolate per-hook failures."""
    for hook in hooks:
        try:
            await hook.after_llm_response(context, response)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle after_llm_response failed for hook %r", _hook_name(hook)
            )


__all__ = [
    "BaseRunLifecycleHook",
    "RevisionRequest",
    "RunLifecycleHook",
    "dispatch_after_llm",
    "dispatch_before_llm",
    "dispatch_error",
    "dispatch_finalize",
    "dispatch_run_start",
    "dispatch_tool_evidence",
]
