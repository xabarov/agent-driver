"""A2.1 — declarative reactive fallback chains.

Why this contract exists
------------------------

The OpenClaude ``utils/hookChains.ts`` pattern lets operators
declare: *"when tool X fails with error matching pattern Y, spawn
fallback agent Z."* This contract pins the Pydantic shape host
applications can author once and ship as default config — no need
to teach the runtime about specific tools / specific errors.

Three rule kinds we support (Phase 1)
-------------------------------------

* **trigger.event = tool_call_failed** with optional ``tool``
  filter — fires when a single planned tool call ends in failure
  (``ToolTraceStatus.FAILED`` or a ``success=False`` envelope).
* **trigger.event = run_failed** — fires on terminal run failure;
  matches every ``RUN_FAILED`` event regardless of tool.
* **trigger.event = tool_call_timed_out** — fires when
  ``TOOL_CALL_COMPLETED`` carries a ``timed_out`` status. Useful
  to differentiate slow-but-buggy from broken-and-fast.

Conditions narrow the match by inspecting the failure: the error
text — ``error_includes`` (case-insensitive substring) or
``error_regex`` (compiled at validation time so a bad regex
doesn't crash at fire time) — and/or structured outcome fields via
``field_equals`` (e.g. ``{"status": "denied"}``), matched against a
tolerant field view of the payload.

Actions for now are limited to ``spawn_fallback`` — emit a
:class:`SubagentSpec` shape the host turns into a real
``run_subagent`` call. Future kinds (``escalate_warning``,
``abort_run`` …) plug in via the same enum.

Cooldown + depth limits
-----------------------

* ``cooldown_seconds`` — minimum monotonic seconds between
  successive fires of the SAME rule in the SAME run. Defaults to
  0 (no cooldown). Use for noisy tools that fail in bursts.
* ``dedup_window_seconds`` — suppress re-firing for the SAME
  trigger *signature* (tool + error text) within the window, while
  still letting a *different* failure fire. Defaults to 0 (no
  dedup). Cooldown gates by rule+time; dedup gates by content.
* ``depth_limit`` — total number of times the rule may fire in
  the current run. Defaults to 1 — the canonical "try ONE
  fallback then give up" semantic. Setting to 0 disables the
  rule (operator can ship a config and toggle it off without
  removing it).

Both invariants are enforced by :class:`HookChainExecutor`; the
contract itself just declares the budgets.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import StrEnum


class HookTriggerEvent(StrEnum):
    """Event kinds the executor watches for."""

    TOOL_CALL_FAILED = "tool_call_failed"
    TOOL_CALL_TIMED_OUT = "tool_call_timed_out"
    RUN_FAILED = "run_failed"


class HookActionType(StrEnum):
    """Action kinds an executor knows how to dispatch.

    Phase 1 ships ``spawn_fallback`` only; the enum is open so
    future actions land cleanly.
    """

    SPAWN_FALLBACK = "spawn_fallback"


class HookTrigger(ContractModel):
    """Which runtime event a rule listens for.

    ``tool`` narrows ``TOOL_CALL_*`` events to one specific tool
    name; absent → match any tool. Has no effect on
    ``RUN_FAILED`` (which carries no tool identity).
    """

    event: HookTriggerEvent
    tool: str | None = None


class HookCondition(ContractModel):
    """Narrow a trigger by inspecting the failed event.

    ``error_includes`` (case-insensitive substring) and
    ``error_regex`` inspect the failure's extracted error text.
    ``field_equals`` matches structured outcome fields by name
    (e.g. ``{"status": "denied"}`` or ``{"tool_name": "bash"}``)
    against a tolerant field view of the payload — top-level keys,
    the normalized ``status``, and the first tool's fields — with
    case-insensitive string comparison. All present fields default
    to ``None``/empty (no narrowing). Multiple conditions AND.
    """

    error_includes: str | None = None
    error_regex: str | None = None
    field_equals: dict[str, str] = Field(default_factory=dict)

    @field_validator("error_regex")
    @classmethod
    def validate_regex_compiles(cls, value: str | None) -> str | None:
        """Compile the regex at validation time so a malformed
        rule fails loudly when the config loads, not silently when
        the rule first matches."""
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"error_regex does not compile: {exc}") from exc
        return value


class HookAction(ContractModel):
    """What the runtime should do when a rule fires.

    Phase 1: only ``SPAWN_FALLBACK`` actions are honoured. The
    ``agent_type`` / ``prompt_template`` / etc. fields shape the
    :class:`SubagentSpec` the host will materialise — they
    intentionally mirror ``SubagentSpec`` so the host code path
    is "build spec from action, call run_subagent".

    ``prompt_template`` may contain ``{placeholder}`` tokens; the
    executor renders them against a host-supplied dict
    (``{tool_name, error_message, original_question, …}``) when
    firing — see :class:`HookChainExecutor.fire`.
    """

    type: HookActionType
    agent_type: str
    prompt_template: str
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    max_tool_calls: int | None = None
    deadline_seconds: float | None = None
    max_cost_usd: float | None = None


class HookRule(ContractModel):
    """One declarative reactive fallback rule.

    ``name`` is a human-readable identifier used in logs and in
    cooldown / depth-limit bookkeeping — must be unique within a
    config (enforced by :class:`HookChainConfig`).
    """

    name: str = Field(..., min_length=1)
    trigger: HookTrigger
    condition: HookCondition = Field(default_factory=HookCondition)
    action: HookAction
    cooldown_seconds: float = 0.0
    dedup_window_seconds: float = 0.0
    depth_limit: int = 1

    @field_validator("cooldown_seconds", "dedup_window_seconds")
    @classmethod
    def validate_non_negative_window(cls, value: float) -> float:
        if value < 0:
            raise ValueError("window seconds must be >= 0")
        return value

    @field_validator("depth_limit")
    @classmethod
    def validate_depth_limit(cls, value: int) -> int:
        if value < 0:
            raise ValueError("depth_limit must be >= 0")
        return value


class HookChainConfig(ContractModel):
    """Bundle of hook rules the executor walks on every event.

    Rules are checked in declaration order — earlier rules win
    if multiple match the same event (mirrors the AllowedPrompt
    matcher's first-match semantic).
    """

    rules: list[HookRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_names(self) -> "HookChainConfig":
        names = [rule.name for rule in self.rules]
        if len(names) != len(set(names)):
            duplicate = next(n for n in names if names.count(n) > 1)
            raise ValueError(f"duplicate rule name: {duplicate!r}")
        return self


__all__ = [
    "HookAction",
    "HookActionType",
    "HookChainConfig",
    "HookCondition",
    "HookRule",
    "HookTrigger",
    "HookTriggerEvent",
]
