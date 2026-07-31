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

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
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

    async def on_run_completed(self, context: "RunContext", *, answer: str) -> None:
        """Called once when a run completes successfully, with the final answer.

        Unlike ``on_finalize`` this fires exactly once, after any goal-gate
        revision loop has settled, so ``answer`` is the answer the user actually
        received. Post-run side effects (long-term memory persistence, audit)
        belong here; anything slow must be scheduled, not awaited — this hook
        sits between the terminal event and the run output being returned.
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

    async def on_run_completed(self, context: "RunContext", *, answer: str) -> None:
        """No-op run-completed hook; override for terminal side effects."""


def _hook_name(hook: RunLifecycleHook) -> str:
    """Best-effort display name for diagnostics."""
    return getattr(hook, "name", None) or type(hook).__name__


def has_hook(hooks: "Iterable[RunLifecycleHook]", method_name: str) -> bool:
    """Whether any hook actually OVERRIDES ``method_name`` (epic 037 phase C).

    The cheap-path gate (reference: hermes ``PluginManager.has_hook``): a caller
    about to build an expensive observer payload — sanitized request/response,
    tool-result projections — first checks ``has_hook(hooks, "after_llm_response")``
    so the uninstrumented default path (no subscriber overrides the method) pays
    nothing. Override detection compares against :class:`BaseRunLifecycleHook`'s
    no-op, so a hook that merely subclasses the base without touching a method is
    correctly treated as "not subscribed" to it. Unknown method names → ``False``.
    """
    base_impl = getattr(BaseRunLifecycleHook, method_name, None)
    if base_impl is None:
        return False
    for hook in hooks:
        impl = getattr(type(hook), method_name, None)
        if impl is not None and impl is not base_impl:
            return True
    return False


# Epic 024 phase C: non-finalize hook dispatches emit a single completed event
# only when the hook was actually slow — visibility without journal noise.
_SLOW_HOOK_EMIT_MS = 250


def _emit_if_slow(
    emit: "Callable[[str, dict[str, Any]], None] | None",
    hook: RunLifecycleHook,
    *,
    phase: str,
    started: float,
) -> None:
    """Emit ``lifecycle_hook_completed`` for a slow non-finalize hook."""
    if emit is None:
        return
    duration_ms = int((time.monotonic() - started) * 1000)
    if duration_ms < _SLOW_HOOK_EMIT_MS:
        return
    emit(
        "lifecycle_hook_completed",
        {"hook": _hook_name(hook), "phase": phase, "duration_ms": duration_ms},
    )


async def dispatch_run_start(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    *,
    emit: "Callable[[str, dict[str, Any]], None] | None" = None,
) -> None:
    """Invoke ``on_run_start`` for each hook; isolate per-hook failures."""
    for hook in hooks:
        started = time.monotonic()
        try:
            await hook.on_run_start(context)
        except Exception:  # pylint: disable=broad-exception-caught
            # One failing observer must not abort the run or block other hooks.
            logger.exception(
                "lifecycle on_run_start failed for hook %r", _hook_name(hook)
            )
        _emit_if_slow(emit, hook, phase="run_start", started=started)


async def dispatch_finalize(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    *,
    answer: str,
    emit: "Callable[[str, dict[str, Any]], None] | None" = None,
    timeout: float | None = None,
) -> "RevisionRequest | None":
    """Invoke ``on_finalize`` for each hook; return the first revision request.

    A hook that raises is logged and skipped (treated as "no revision"), so a
    faulty goal-gate cannot wedge the run at finalize.

    ``emit``, when provided, receives ``(event_type, payload)`` pairs bracketing
    each hook that actually overrides ``on_finalize`` — this is what makes slow
    finalize work (an LLM goal-gate grading the answer) visible to the host's
    event stream instead of an unexplained gap before the terminal event.

    ``timeout`` (epic 024, per-hook seconds) bounds each ``on_finalize``: a hook
    that exceeds it fails open — the answer is accepted, the hook's revision (if
    any) is lost, and ``lifecycle_hook_timed_out`` is emitted. Finalize hooks may
    legitimately block (a goal-gate can demand a revision), but never unboundedly:
    the measured 22-139s post-final tails came exactly from unbudgeted awaits here.
    """
    # Local import: observability stays optional and no-op when tracing is off
    # (same pattern as the llm-step span). Epic 025 phase D: each overriding
    # finalize hook gets its own span so a slow grader is a colored span on the
    # trace, not an unexplained gap before the terminal event.
    from agent_driver.observability.openinference import (  # noqa: PLC0415
        SPAN_KIND_CHAIN,
        oi_span,
        record_status,
    )

    revision: RevisionRequest | None = None
    for hook in hooks:
        overrides_finalize = (
            getattr(type(hook), "on_finalize", None)
            is not BaseRunLifecycleHook.on_finalize
        )
        hook_emit = emit if (emit is not None and overrides_finalize) else None
        if hook_emit is not None:
            hook_emit(
                "lifecycle_hook_started",
                {"hook": _hook_name(hook), "phase": "finalize"},
            )
        started = time.monotonic()
        timed_out = False
        span_cm = (
            oi_span(f"lifecycle_hook {_hook_name(hook)}", kind=SPAN_KIND_CHAIN)
            if overrides_finalize
            else None
        )
        span = span_cm.__enter__() if span_cm is not None else None
        try:
            call = hook.on_finalize(context, answer=answer)
            result = await (
                asyncio.wait_for(call, timeout) if timeout is not None else call
            )
        except asyncio.TimeoutError:
            timed_out = True
            result = None
            logger.warning(
                "lifecycle on_finalize timed out after %.1fs for hook %r; "
                "failing open (answer accepted)",
                timeout,
                _hook_name(hook),
            )
            if hook_emit is not None:
                hook_emit(
                    "lifecycle_hook_timed_out",
                    {
                        "hook": _hook_name(hook),
                        "phase": "finalize",
                        "timeout_seconds": timeout,
                    },
                )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle on_finalize failed for hook %r", _hook_name(hook)
            )
            result = None
        finally:
            if span_cm is not None:
                record_status(span, ok=not timed_out)
                span_cm.__exit__(None, None, None)
        if hook_emit is not None and not timed_out:
            hook_emit(
                "lifecycle_hook_completed",
                {
                    "hook": _hook_name(hook),
                    "phase": "finalize",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "requested_revision": result is not None,
                },
            )
        if result is not None and revision is None:
            revision = result
    return revision


async def dispatch_run_completed(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    *,
    answer: str,
    emit: "Callable[[str, dict[str, Any]], None] | None" = None,
) -> None:
    """Invoke ``on_run_completed`` for each hook; isolate per-hook failures.

    Hooks here must schedule slow work, not await it (terminal-phase contract,
    docs/terminal-phase-contract.md) — a slow hook shows up via ``emit``.
    """
    for hook in hooks:
        started = time.monotonic()
        try:
            await hook.on_run_completed(context, answer=answer)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle on_run_completed failed for hook %r", _hook_name(hook)
            )
        _emit_if_slow(emit, hook, phase="run_completed", started=started)


async def dispatch_tool_evidence(
    hooks: Iterable[RunLifecycleHook],
    context: "RunContext",
    envelopes: "list[ToolResultEnvelope]",
    *,
    emit: "Callable[[str, dict[str, Any]], None] | None" = None,
) -> "FinalizeNow | None":
    """Invoke ``on_tool_evidence`` for each hook; return the first finalize directive.

    A hook that raises is logged and skipped (treated as "continue"), so a faulty
    early-finalize hook degrades to normal looping rather than wedging the run.
    """
    directive: "FinalizeNow | None" = None
    for hook in hooks:
        started = time.monotonic()
        try:
            result = await hook.on_tool_evidence(context, envelopes)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle on_tool_evidence failed for hook %r", _hook_name(hook)
            )
            _emit_if_slow(emit, hook, phase="tool_evidence", started=started)
            continue
        _emit_if_slow(emit, hook, phase="tool_evidence", started=started)
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


def _is_degenerate_request(request: Any) -> bool:
    """Whether a select-context replacement would blank the prompt (epic 044 B).

    A context-selection hook that filters everything out returns a request with no
    usable turn — the hermes ``all([]) is True`` trap. Reject a replacement that is
    not shaped like a request or whose non-system messages are all gone, so the
    chain falls open to the prior request instead of dispatching an empty prompt.
    """
    messages = getattr(request, "messages", None)
    if messages is None:
        return True
    for message in messages:
        role = getattr(message, "role", None)
        role_value = str(getattr(role, "value", role) or "")
        content = getattr(message, "content", None)
        if role_value != "system" and str(content or "").strip():
            return False
    return True


async def dispatch_before_llm(
    hooks: Iterable[RunLifecycleHook], context: "RunContext", request: Any
) -> Any:
    """Chain ``before_llm_request`` hooks; return the (possibly transformed) request.

    A hook that raises is logged and skipped, leaving the request as produced by
    the prior hooks — a faulty transform degrades to a no-op rather than failing
    the LLM call. A hook that returns a degenerate replacement (no non-system
    message survived) is likewise rejected and the prior request is kept: a
    select-context that filters everything out must fall open, not blank the prompt.
    """
    for hook in hooks:
        try:
            replacement = await hook.before_llm_request(context, request)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "lifecycle before_llm_request failed for hook %r", _hook_name(hook)
            )
            continue
        if replacement is None:
            continue
        if _is_degenerate_request(replacement):
            logger.warning(
                "lifecycle before_llm_request for hook %r returned a degenerate "
                "request (no non-system message); keeping the prior request",
                _hook_name(hook),
            )
            continue
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
    "dispatch_run_completed",
    "dispatch_run_start",
    "dispatch_tool_evidence",
]
