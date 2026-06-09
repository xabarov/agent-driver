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

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_driver.contracts.events import RuntimeEvent
    from agent_driver.contracts.runtime import AgentRunOutput
    from agent_driver.llm.contracts import LlmRequest, LlmResponse
    from agent_driver.runtime.single_agent.types import RunContext


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

    async def on_finalize(self, context: "RunContext", *, answer: str) -> None:
        """Called once when a run reaches its terminal final answer."""

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

    async def on_finalize(self, context: "RunContext", *, answer: str) -> None:
        """No-op finalize hook; override to react to the final answer."""

    async def on_error(
        self,
        context: "RunContext",
        *,
        output: "AgentRunOutput",
        events: "list[RuntimeEvent]",
    ) -> None:
        """No-op error hook; override to react to a failed run."""


async def dispatch_run_start(
    hooks: Iterable[RunLifecycleHook], context: "RunContext"
) -> None:
    """Invoke ``on_run_start`` for each hook in order."""
    for hook in hooks:
        await hook.on_run_start(context)


async def dispatch_finalize(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", *, answer: str
) -> None:
    """Invoke ``on_finalize`` for each hook in order."""
    for hook in hooks:
        await hook.on_finalize(context, answer=answer)


async def dispatch_error(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    *,
    output: "AgentRunOutput",
    events: "list[RuntimeEvent]",
) -> None:
    """Invoke ``on_error`` for each hook in order."""
    for hook in hooks:
        await hook.on_error(context, output=output, events=events)


async def dispatch_before_llm(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", request: Any
) -> Any:
    """Chain ``before_llm_request`` hooks; return the (possibly transformed) request."""
    for hook in hooks:
        replacement = await hook.before_llm_request(context, request)
        if replacement is not None:
            request = replacement
    return request


async def dispatch_after_llm(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", response: Any
) -> None:
    """Invoke ``after_llm_response`` for each hook in order."""
    for hook in hooks:
        await hook.after_llm_response(context, response)


__all__ = [
    "BaseRunLifecycleHook",
    "RunLifecycleHook",
    "dispatch_after_llm",
    "dispatch_before_llm",
    "dispatch_error",
    "dispatch_finalize",
    "dispatch_run_start",
]
