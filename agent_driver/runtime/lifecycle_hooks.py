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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext


@runtime_checkable
class RunLifecycleHook(Protocol):
    """Observer of run boundaries. Either method may be a no-op."""

    name: str

    async def on_run_start(self, context: "RunContext") -> None:
        """Called once when a run begins (before the first LLM call)."""

    async def on_finalize(self, context: "RunContext", *, answer: str) -> None:
        """Called once when a run reaches its terminal final answer."""


class BaseRunLifecycleHook:
    """Convenience base with no-op implementations; override what you need."""

    name: str = "base_run_lifecycle_hook"

    async def on_run_start(self, context: "RunContext") -> None:
        """No-op run-start hook; override to react to run start."""

    async def on_finalize(self, context: "RunContext", *, answer: str) -> None:
        """No-op finalize hook; override to react to the final answer."""


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


__all__ = [
    "BaseRunLifecycleHook",
    "RunLifecycleHook",
    "dispatch_finalize",
    "dispatch_run_start",
]
