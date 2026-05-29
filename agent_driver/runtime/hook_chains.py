"""A2.1 runtime executor for hook-chain rules.

Architecture
------------

Reactive fallbacks live OUTSIDE the agent runner: the runner emits
events; the executor inspects them; on match it returns a
:class:`FallbackSpec` describing what to spawn. The HOST is the one
that decides *when* and *how* to spawn (e.g. inside the orchestrator
loop, between runs, as a side-task) — keeping the SDK free of any
"now spawn this subagent" plumbing that would couple it to a
specific orchestration pattern.

Concretely the host calls :meth:`HookChainExecutor.observe` for
every event it cares about and gets back zero or more
:class:`FallbackSpec` objects. The host then converts each into a
:class:`SubagentSpec` (cheap — the action already mirrors the spec
shape) and invokes :func:`run_subagent`.

Cooldown + depth enforcement
----------------------------

The executor tracks per-rule firing state in two dicts keyed by
rule name. Both are populated on a successful match:

* ``_fired_count[name]`` increments each fire; rule is skipped
  once ``_fired_count >= depth_limit``.
* ``_last_fired_at[name]`` records ``time.monotonic()`` at fire;
  rule is skipped while ``now - last_fired_at < cooldown_seconds``.

Per-rule state means firing rule A does NOT reset rule B's
cooldown — they're independent budgets.

Placeholder substitution
------------------------

``prompt_template`` is rendered with :meth:`str.format_map` against
the ``placeholders`` dict the host supplies on :meth:`observe`.
Missing placeholder names render as the empty string (we want a
typo in the template to leave an obvious gap in the prompt rather
than crash the fallback). Use
:func:`HookChainExecutor.default_placeholders` for the standard
``{tool_name, error_message, original_question}`` set the host can
build from event payload + run input.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.hook_chains import (
    HookActionType,
    HookChainConfig,
    HookCondition,
    HookRule,
    HookTriggerEvent,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FallbackSpec:
    """One concrete fallback the host should spawn.

    Carries the resolved spawn shape (post-template-render) plus
    enough provenance for the host to log / observe the chain.

    The host's typical usage:

    .. code-block:: python

       for fallback in executor.observe(event, placeholders=...):
           spec = SubagentSpec(
               agent_type=fallback.agent_type,
               prompt=fallback.prompt,
               allowed_tools=fallback.allowed_tools,
               tool_choice=fallback.tool_choice,
               response_format=fallback.response_format,
               max_tool_calls=fallback.max_tool_calls,
               deadline_seconds=fallback.deadline_seconds,
               max_cost_usd=fallback.max_cost_usd,
               app_metadata={
                   "fallback_rule": fallback.rule_name,
                   "fallback_for": fallback.triggered_by,
               },
           )
           result = await run_subagent(parent_agent, spec, ...)
    """

    rule_name: str
    """Name of the rule that matched — for logs + observability."""

    triggered_by: str
    """Short reason string: "tool_call_failed:chart_vegalite",
    "run_failed", etc. Helps operators see WHY the fallback fired."""

    agent_type: str
    prompt: str
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    max_tool_calls: int | None = None
    deadline_seconds: float | None = None
    max_cost_usd: float | None = None


@dataclass(slots=True)
class _RuleState:
    """Per-rule firing bookkeeping."""

    fire_count: int = 0
    last_fired_at: float | None = None


class HookChainExecutor:
    """Walks a :class:`HookChainConfig` over each event the host
    feeds it; returns zero or more :class:`FallbackSpec` to spawn.

    Lifetime: one executor per agent run. Cooldown / depth budgets
    are scoped to the executor instance — long-lived agents should
    create a fresh executor per run so depth limits reset cleanly.
    """

    def __init__(
        self,
        config: HookChainConfig,
        *,
        now: "callable[[], float] | None" = None,  # type: ignore[name-defined]
    ) -> None:
        self._config = config
        self._state: dict[str, _RuleState] = defaultdict(_RuleState)
        # Injectable clock so tests can pin "elapsed since last fire"
        # without sleeping. Defaults to monotonic time.
        self._now = now or time.monotonic

    @staticmethod
    def default_placeholders(
        *,
        tool_name: str | None = None,
        error_message: str | None = None,
        original_question: str | None = None,
        tool_args: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Standard placeholder dict for ``prompt_template``.

        Use this in the host's ``observe`` call sites so every
        consumer ships the same vocabulary. Additional placeholders
        can be merged in — the executor doesn't validate the dict's
        shape.
        """
        import json

        return {
            "tool_name": tool_name or "",
            "error_message": error_message or "",
            "original_question": original_question or "",
            "tool_args": json.dumps(tool_args or {}, ensure_ascii=False),
        }

    def observe(
        self,
        event: RuntimeEvent,
        *,
        placeholders: Mapping[str, str] | None = None,
    ) -> list[FallbackSpec]:
        """Feed one event; return zero or more fallbacks to spawn.

        ``placeholders`` is rendered into each rule's
        ``prompt_template`` via ``str.format_map``. Missing keys
        render as the empty string (see module docstring).
        """
        triggered_by = self._triggered_by_for_event(event)
        if triggered_by is None:
            return []

        results: list[FallbackSpec] = []
        for rule in self._config.rules:
            if not self._trigger_matches(rule, event):
                continue
            if not self._condition_matches(rule.condition, event):
                continue
            if not self._budget_allows(rule):
                continue
            if rule.action.type != HookActionType.SPAWN_FALLBACK:
                # Unknown / future action kinds — log + skip rather
                # than raise; an SDK upgrade may add new kinds the
                # host hasn't taught the executor yet.
                logger.warning(
                    "hook_chain rule %r has unsupported action type %r; "
                    "skipping",
                    rule.name,
                    rule.action.type,
                )
                continue

            self._record_fire(rule)
            results.append(
                self._render_fallback(
                    rule=rule,
                    placeholders=placeholders or {},
                    triggered_by=triggered_by,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _trigger_matches(rule: HookRule, event: RuntimeEvent) -> bool:
        trigger = rule.trigger
        if trigger.event == HookTriggerEvent.RUN_FAILED:
            return event.type == RuntimeEventType.RUN_FAILED
        if trigger.event == HookTriggerEvent.TOOL_CALL_FAILED:
            if event.type != RuntimeEventType.TOOL_CALL_COMPLETED:
                return False
            if not _payload_indicates_failure(event.payload or {}):
                return False
            if trigger.tool is None:
                return True
            return _payload_matches_tool(event.payload or {}, trigger.tool)
        if trigger.event == HookTriggerEvent.TOOL_CALL_TIMED_OUT:
            if event.type != RuntimeEventType.TOOL_CALL_COMPLETED:
                return False
            if not _payload_indicates_timeout(event.payload or {}):
                return False
            if trigger.tool is None:
                return True
            return _payload_matches_tool(event.payload or {}, trigger.tool)
        return False

    @staticmethod
    def _condition_matches(
        condition: HookCondition, event: RuntimeEvent
    ) -> bool:
        error_text = _extract_error_text(event.payload or {})
        if condition.error_includes is not None:
            if condition.error_includes.lower() not in error_text.lower():
                return False
        if condition.error_regex is not None:
            if not re.search(condition.error_regex, error_text):
                return False
        return True

    def _budget_allows(self, rule: HookRule) -> bool:
        if rule.depth_limit == 0:
            return False  # explicitly disabled
        state = self._state[rule.name]
        if state.fire_count >= rule.depth_limit:
            return False
        if (
            state.last_fired_at is not None
            and rule.cooldown_seconds > 0
            and self._now() - state.last_fired_at < rule.cooldown_seconds
        ):
            return False
        return True

    def _record_fire(self, rule: HookRule) -> None:
        state = self._state[rule.name]
        state.fire_count += 1
        state.last_fired_at = self._now()

    # ------------------------------------------------------------------
    # Render helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_fallback(
        *,
        rule: HookRule,
        placeholders: Mapping[str, str],
        triggered_by: str,
    ) -> FallbackSpec:
        action = rule.action
        prompt = _safe_format(action.prompt_template, placeholders)
        return FallbackSpec(
            rule_name=rule.name,
            triggered_by=triggered_by,
            agent_type=action.agent_type,
            prompt=prompt,
            allowed_tools=action.allowed_tools,
            denied_tools=action.denied_tools,
            tool_choice=action.tool_choice,
            response_format=action.response_format,
            max_tool_calls=action.max_tool_calls,
            deadline_seconds=action.deadline_seconds,
            max_cost_usd=action.max_cost_usd,
        )

    @staticmethod
    def _triggered_by_for_event(event: RuntimeEvent) -> str | None:
        """Short label used in :class:`FallbackSpec.triggered_by`.

        Returns ``None`` for events the executor doesn't care
        about — short-circuits the rule loop without spending CPU
        on every event the runtime emits.
        """
        if event.type == RuntimeEventType.RUN_FAILED:
            return "run_failed"
        if event.type == RuntimeEventType.TOOL_CALL_COMPLETED:
            tool = _extract_tool_name(event.payload or {})
            if _payload_indicates_failure(event.payload or {}):
                return f"tool_call_failed:{tool or 'unknown'}"
            if _payload_indicates_timeout(event.payload or {}):
                return f"tool_call_timed_out:{tool or 'unknown'}"
        return None


# ----------------------------------------------------------------------
# Payload introspection helpers — tolerant of multiple shapes the
# runtime + governed executor emit.
# ----------------------------------------------------------------------


def _payload_indicates_failure(payload: dict[str, Any]) -> bool:
    statuses = payload.get("statuses")
    if isinstance(statuses, list):
        return any(
            isinstance(s, str) and s.lower() in {"failed", "denied"}
            for s in statuses
        )
    status = payload.get("status")
    if isinstance(status, str):
        return status.lower() in {"failed", "denied"}
    # Some envelopes carry a top-level ``success=False`` flag.
    return payload.get("success") is False


def _payload_indicates_timeout(payload: dict[str, Any]) -> bool:
    statuses = payload.get("statuses")
    if isinstance(statuses, list):
        return any(
            isinstance(s, str) and s.lower() == "timed_out"
            for s in statuses
        )
    status = payload.get("status")
    if isinstance(status, str):
        return status.lower() == "timed_out"
    return False


def _payload_matches_tool(payload: dict[str, Any], tool: str) -> bool:
    name = _extract_tool_name(payload)
    return name == tool


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        first = tools[0]
        if isinstance(first, dict):
            candidate = first.get("tool_name")
            if isinstance(candidate, str):
                return candidate
    direct = payload.get("tool_name")
    if isinstance(direct, str):
        return direct
    return None


def _extract_error_text(payload: dict[str, Any]) -> str:
    """Best-effort error blob — concatenates anywhere a string
    error might land so ``error_includes`` / ``error_regex`` can
    inspect it without the rule author having to know the exact
    payload shape."""
    pieces: list[str] = []
    for key in ("error", "error_message", "reason", "summary"):
        value = payload.get(key)
        if isinstance(value, str):
            pieces.append(value)
    # Tool-stage payloads sometimes wrap per-call error in ``tools[i].error``.
    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                err = tool.get("error")
                if isinstance(err, str):
                    pieces.append(err)
    return " ".join(pieces)


def _safe_format(template: str, placeholders: Mapping[str, str]) -> str:
    """``str.format_map`` with missing-key fallback to empty string.

    Misspelled placeholders in a rule's template leave an obvious
    gap in the rendered prompt instead of crashing at fire time —
    surfaces as a debug-able prompt the operator can fix without
    losing the fallback run."""

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""

    return template.format_map(_SafeDict(placeholders))


__all__ = [
    "FallbackSpec",
    "HookChainExecutor",
]
