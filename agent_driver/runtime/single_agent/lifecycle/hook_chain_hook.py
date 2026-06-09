"""Lifecycle hook that drives declarative hook-chain self-healing.

Bridges the dormant :class:`HookChainExecutor` into the run lifecycle: on a
failed run the hook replays the run's events through a fresh per-run executor
and hands each matched :class:`FallbackSpec` to a host-supplied ``spawn``
callback. Spawning stays the host's responsibility (it owns ``run_subagent``
and the agent instance), keeping the runtime free of orchestration plumbing —
the same boundary the executor was designed around.

A fresh executor per run scopes cooldown / depth budgets to that run and
avoids fallback recursion across runs.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from agent_driver.contracts.hook_chains import HookChainConfig
from agent_driver.runtime.hook_chains import (
    FallbackSpec,
    HookChainExecutor,
    placeholders_for_event,
)
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook

if TYPE_CHECKING:
    from agent_driver.contracts.events import RuntimeEvent
    from agent_driver.contracts.runtime import AgentRunOutput
    from agent_driver.runtime.single_agent.types import RunContext

logger = logging.getLogger(__name__)

# Host callback that materializes one fallback (e.g. into a SubagentSpec and a
# run_subagent call). Returning is ignored; raising is isolated per-spawn.
FallbackSpawn = Callable[[FallbackSpec, "RunContext"], Awaitable[object]]


class HookChainLifecycleHook(BaseRunLifecycleHook):
    """Spawn declarative fallbacks when a run fails."""

    name = "hook_chains"

    def __init__(
        self,
        config: HookChainConfig,
        spawn: FallbackSpawn,
        *,
        now: "Callable[[], float] | None" = None,
    ) -> None:
        self._config = config
        self._spawn = spawn
        self._now = now

    async def on_error(
        self,
        context: "RunContext",
        *,
        output: "AgentRunOutput",
        events: "list[RuntimeEvent]",
    ) -> None:
        _ = output
        if not self._config.rules:
            return
        # Fresh executor per run: cooldown/depth budgets are run-scoped.
        executor = HookChainExecutor(self._config, now=self._now)
        original_question = context.run_input.input or ""
        fallbacks: list[FallbackSpec] = []
        for event in events:
            placeholders = placeholders_for_event(
                event, original_question=original_question
            )
            fallbacks.extend(executor.observe(event, placeholders=placeholders))
        for fallback in fallbacks:
            try:
                await self._spawn(fallback, context)
            except Exception:  # pylint: disable=broad-exception-caught
                # One failing fallback must not drop the others.
                logger.exception(
                    "hook-chain fallback spawn failed for rule %r",
                    fallback.rule_name,
                )


__all__ = ["FallbackSpawn", "HookChainLifecycleHook"]
