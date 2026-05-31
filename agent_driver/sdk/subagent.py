"""Public ``run_subagent`` API for Python-driven child agent spawn.

Why this exists
---------------

The existing ``subagents/`` machinery (``SubagentTaskSpec`` /
``SubagentGroupSpec`` / ``execute_subagent_group_sync``) is reachable
only from inside the runtime — the model emits a planning tool call,
the tool-stage code parses it into a group spec, then fans out. There
is no public path for a Python caller (e.g. an excel_ai orchestrator)
to say: *"run a constrained child agent right here, give me back its
answer + tools + cost"*.

This module fills that gap. It is intentionally small: a dataclass
spec, a dataclass result, and one async function that wraps
``Agent.run`` with the right plumbing. Larger sub-agent features —
shared system-prompt cache for fork (B0.2), parent-cost aggregation,
parallel groups — build on this primitive.

Design choices
--------------

* ``SubagentSpec`` is a **frozen dataclass**, not a Pydantic
  ``ContractModel``. It is a request shape consumed in-process; we
  don't store / transport / checkpoint it. Keeping it dataclass avoids
  the validator overhead and lets us put ``frozen=True, slots=True``
  on it.
* ``run_subagent`` takes ``parent_abort_handle`` separately rather than
  inside the spec — the spec stays JSON-friendly (a future feature can
  serialise it for debug logging) while the handle carries the
  non-serialisable cross-thread lock.
* ``response_format`` flows through as-is to ``AgentRunInput`` — the
  provider adapter handles the wire shape. Callers wanting strong
  pydantic validation should run instructor on the resulting
  ``structured_output`` (or use the ``StructuredExtractor`` shortcut
  in excel_ai's ``llm/structured/extractor.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_driver.contracts.enums import (
    AgentProfile,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    ToolPolicyMode,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolPolicyInput, ToolTrace
from agent_driver.contracts.usage import UsageSummary
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubagentToolPolicy:
    """Tool exposure and provider tool-choice controls for a child run."""

    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] | None = None
    tool_choice: str | dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SubagentOutputPolicy:
    """Output and profile controls for a child run."""

    response_format: dict[str, Any] | None = None
    agent_profile: AgentProfile = AgentProfile.TOOL_CALLING
    app_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubagentLimits:
    """Execution ceilings for one child run."""

    max_tool_calls: int | None = None
    deadline_seconds: float | None = None
    max_cost_usd: float | None = None


class SubagentSpec:
    """Declarative spec for one Python-driven child agent run.

    Conceptually: "run an agent that does exactly THIS task, with these
    constraints, and give me back the result". Used by the parent
    orchestrator (in our case excel_ai) to compose specialised stages.

    Attributes
    ----------
    agent_type:
        Free-form label for trace / observability (e.g.
        ``"chart_implementer"``, ``"data_explorer"``). Surfaces in
        ``SubagentResult.agent_type`` and in any future
        ``SubagentRun`` audit record built from this spec.
    prompt:
        User-role message body. Becomes the last message of the child's
        ``AgentRunInput.messages``.
    system_prompt:
        Optional system message. When provided, it's prepended as a
        ``system`` role ``ChatMessage`` to the child's input messages.
        Required for fork-style cache reuse (B0.2 will populate this
        from the parent's rendered system prompt).
    allowed_tools / denied_tools:
        Tool-policy allowlist / denylist. Flows into
        ``AgentRunInput.tool_policy.allowed_tools`` /
        ``denied_tools`` (see the schema-filter pattern landed in
        commit 55f3dae). The child physically cannot see — and
        therefore cannot call — tools outside the allowlist.
    tool_choice:
        Provider-level tool forcing (``"auto"`` / ``"required"`` /
        ``"none"`` / ``{"type": "tool", "name": "X"}``). See
        ``docs/patterns/forcing-tool-calls.md``.
    response_format:
        Provider-level structured output enforcement (passed as
        ``LlmRequest.response_format``).
    max_tool_calls / deadline_seconds:
        Per-child caps. The child fails terminal with
        ``TOOL_POLICY_DENIED`` / ``DEADLINE_EXCEEDED`` accordingly.
    agent_profile:
        ``AgentProfile`` for the child. Defaults to ``TOOL_CALLING``
        because that matches every consumer we have (qwen / gpt /
        deepseek all speak OpenAI tools). Override for ``CODE_AGENT``
        in code-style stages.

    The constructor keeps the original flat keyword arguments for
    compatibility, while internally grouping policy, output and limits so the
    public spec stays readable as it grows.
    """

    __slots__ = (
        "agent_type",
        "prompt",
        "system_prompt",
        "_tool_policy",
        "_output_policy",
        "_limits",
        "_frozen",
    )
    _tool_policy: SubagentToolPolicy
    _output_policy: SubagentOutputPolicy
    _limits: SubagentLimits
    agent_type: str
    prompt: str
    system_prompt: str | None

    def __init__(
        self,
        *,
        agent_type: str,
        prompt: str,
        system_prompt: str | None = None,
        allowed_tools: tuple[str, ...] | None = None,
        denied_tools: tuple[str, ...] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tool_calls: int | None = None,
        deadline_seconds: float | None = None,
        agent_profile: AgentProfile = AgentProfile.TOOL_CALLING,
        app_metadata: dict[str, Any] | None = None,
        max_cost_usd: float | None = None,
        tool_policy: SubagentToolPolicy | None = None,
        output_policy: SubagentOutputPolicy | None = None,
        limits: SubagentLimits | None = None,
    ) -> None:
        object.__setattr__(self, "agent_type", agent_type)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "system_prompt", system_prompt)
        object.__setattr__(
            self,
            "_tool_policy",
            tool_policy
            or SubagentToolPolicy(
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                tool_choice=tool_choice,
            ),
        )
        object.__setattr__(
            self,
            "_output_policy",
            output_policy
            or SubagentOutputPolicy(
                response_format=response_format,
                agent_profile=agent_profile,
                app_metadata=dict(app_metadata or {}),
            ),
        )
        object.__setattr__(
            self,
            "_limits",
            limits
            or SubagentLimits(
                max_tool_calls=max_tool_calls,
                deadline_seconds=deadline_seconds,
                max_cost_usd=max_cost_usd,
            ),
        )
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"can't set attribute {name!r} on frozen SubagentSpec")
        object.__setattr__(self, name, value)

    def with_system_prompt(self, system_prompt: str | None) -> "SubagentSpec":
        """Return a copy with a different system prompt."""
        return SubagentSpec(
            agent_type=self.agent_type,
            prompt=self.prompt,
            system_prompt=system_prompt,
            tool_policy=self._tool_policy,
            output_policy=self._output_policy,
            limits=self._limits,
        )

    @property
    def allowed_tools(self) -> tuple[str, ...] | None:
        """Optional allowed tool names for the child."""
        return self._tool_policy.allowed_tools

    @property
    def denied_tools(self) -> tuple[str, ...] | None:
        """Optional denied tool names for the child."""
        return self._tool_policy.denied_tools

    @property
    def tool_choice(self) -> str | dict[str, Any] | None:
        """Provider-level tool-choice forcing for the child."""
        return self._tool_policy.tool_choice

    @property
    def response_format(self) -> dict[str, Any] | None:
        """Provider-level response format for the child."""
        return self._output_policy.response_format

    @property
    def max_tool_calls(self) -> int | None:
        """Maximum tool calls for the child run."""
        return self._limits.max_tool_calls

    @property
    def deadline_seconds(self) -> float | None:
        """Deadline in seconds for the child run."""
        return self._limits.deadline_seconds

    @property
    def agent_profile(self) -> AgentProfile:
        """Runtime agent profile for the child."""
        return self._output_policy.agent_profile

    @property
    def app_metadata(self) -> dict[str, Any]:
        """JSON-friendly metadata merged into the child run input."""
        return dict(self._output_policy.app_metadata)

    @property
    def max_cost_usd(self) -> float | None:
        """B2.2 — soft cost ceiling for the child run.

        When set, a background watchdog polls the child's event log,
        sums ``cost_usd_estimate`` from every ``llm_call_completed``
        event, and flips the child's :class:`RunAbortHandle` with
        ``reason="budget_exceeded"`` once the running total reaches
        ``max_cost_usd``.
        """
        return self._limits.max_cost_usd


@dataclass(frozen=True, slots=True)
class SubagentResultIdentity:
    """Run identity fields for a subagent result."""

    child_run_id: str
    parent_run_id: str | None
    agent_type: str


@dataclass(frozen=True, slots=True)
class SubagentResultOutcome:
    """Terminal status and answer fields for a subagent result."""

    status: RunStatus
    terminal_reason: TerminalReason | None
    answer: str | None
    structured_output: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class SubagentResultArtifacts:
    """Trace, usage and raw output fields for a subagent result."""

    tool_trace: tuple[ToolTrace, ...]
    usage: UsageSummary | None
    raw_output: Any


class SubagentResult:
    """Normalised outcome of one ``run_subagent`` call.

    Carries the bits a parent orchestrator actually consumes — answer,
    tool trace, usage. Full ``AgentRunOutput`` is also returned via
    ``raw_output`` for callers that need the rare fields (warnings,
    memory_projection, etc.).
    """

    __slots__ = ("_identity", "_outcome", "_artifacts", "_frozen")
    _identity: SubagentResultIdentity
    _outcome: SubagentResultOutcome
    _artifacts: SubagentResultArtifacts

    def __init__(
        self,
        *,
        child_run_id: str,
        parent_run_id: str | None,
        agent_type: str,
        status: RunStatus,
        terminal_reason: TerminalReason | None,
        answer: str | None,
        structured_output: dict[str, Any] | None,
        tool_trace: tuple[ToolTrace, ...],
        usage: UsageSummary | None,
        raw_output: Any,
    ) -> None:
        object.__setattr__(
            self,
            "_identity",
            SubagentResultIdentity(
                child_run_id=child_run_id,
                parent_run_id=parent_run_id,
                agent_type=agent_type,
            ),
        )
        object.__setattr__(
            self,
            "_outcome",
            SubagentResultOutcome(
                status=status,
                terminal_reason=terminal_reason,
                answer=answer,
                structured_output=structured_output,
            ),
        )
        object.__setattr__(
            self,
            "_artifacts",
            SubagentResultArtifacts(
                tool_trace=tool_trace,
                usage=usage,
                raw_output=raw_output,
            ),
        )
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(
                f"can't set attribute {name!r} on frozen SubagentResult"
            )
        object.__setattr__(self, name, value)

    @property
    def child_run_id(self) -> str:
        """Child run id."""
        return self._identity.child_run_id

    @property
    def parent_run_id(self) -> str | None:
        """Parent run id, if supplied."""
        return self._identity.parent_run_id

    @property
    def agent_type(self) -> str:
        """Free-form child agent type."""
        return self._identity.agent_type

    @property
    def status(self) -> RunStatus:
        """Terminal child status."""
        return self._outcome.status

    @property
    def terminal_reason(self) -> TerminalReason | None:
        """Terminal child reason, if available."""
        return self._outcome.terminal_reason

    @property
    def answer(self) -> str | None:
        """Child answer text."""
        return self._outcome.answer

    @property
    def structured_output(self) -> dict[str, Any] | None:
        """Parsed structured output when requested and valid."""
        return self._outcome.structured_output

    @property
    def tool_trace(self) -> tuple[ToolTrace, ...]:
        """Child tool trace."""
        return self._artifacts.tool_trace

    @property
    def usage(self) -> UsageSummary | None:
        """Child usage summary."""
        return self._artifacts.usage

    @property
    def raw_output(self) -> Any:
        """Full AgentRunOutput, kept as Any to avoid a circular import."""
        return self._artifacts.raw_output


async def run_subagent(
    parent: Agent,
    spec: SubagentSpec,
    *,
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
    tool_gate: ToolGate | None = None,
) -> SubagentResult:
    """Spawn one child agent and await its result.

    The child shares the parent ``Agent``'s provider, tool registry,
    and runner config — only the per-call shape (prompt, allowlist,
    tool_choice, response_format, budgets, profile) is overridden by
    ``spec``.

    Cancellation
    ------------
    When ``parent_abort_handle`` is provided, the child gets
    ``parent_abort_handle.child()`` — a weakly-linked handle. Aborting
    the parent cascades to the child at the next child step boundary.
    Aborting the child does NOT propagate up to the parent.

    Tool gate
    ---------
    When ``tool_gate`` is provided, it is forwarded to the child
    :meth:`Agent.run` call. The gate sees the child's planned tool
    calls AFTER the static policy passes ALLOW, identical to the
    parent's contract — useful when the parent's gate captures
    organisation-wide policy (destructive ops, large fetches) that
    the child should also respect.

    Structured output extraction
    ----------------------------
    If ``spec.response_format`` is set, the function attempts to
    ``json.loads`` the child's ``answer`` and stores the parsed dict on
    ``SubagentResult.structured_output``. On parse failure the field is
    ``None`` and the raw answer is still in ``SubagentResult.answer`` —
    callers wanting strict validation should run a pydantic model over
    the dict on their side (or hit the ``StructuredExtractor``
    shortcut, which uses instructor end-to-end).
    """
    messages: list[ChatMessage] = []
    if spec.system_prompt:
        messages.append(ChatMessage(role="system", content=spec.system_prompt))
    messages.append(ChatMessage(role="user", content=spec.prompt))

    app_metadata = {
        "subagent_origin": "child",
        "agent_type": spec.agent_type,
        **(
            {"parent_run_id": parent_run_id}
            if parent_run_id is not None
            else {}
        ),
        **spec.app_metadata,
    }

    tool_policy = ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        allowed_tools=(
            list(spec.allowed_tools) if spec.allowed_tools is not None else None
        ),
        denied_tools=list(spec.denied_tools) if spec.denied_tools else None,
    )

    child_input = AgentRunInput(
        messages=messages,
        run_id=f"sub_{uuid4().hex[:12]}",
        agent_id=f"{parent.defaults.agent_id}.{spec.agent_type}",
        graph_preset=parent.defaults.graph_preset,
        agent_profile=spec.agent_profile,
        stream=False,
        max_tool_calls=spec.max_tool_calls,
        deadline_seconds=spec.deadline_seconds,
        tool_choice=spec.tool_choice,
        response_format=spec.response_format,
        tool_policy=tool_policy,
        app_metadata=app_metadata,
    )

    child_abort = (
        parent_abort_handle.child() if parent_abort_handle is not None else None
    )

    # B2.2 — when a cost budget is set, ensure we have an abort handle
    # the watchdog can flip. If the caller didn't supply a parent
    # handle we mint a standalone one — the child still gets canceled
    # cleanly on budget violation; only the parent->child cascade is
    # absent (which is correct, since there's no parent to cascade to).
    if spec.max_cost_usd is not None and child_abort is None:
        child_abort = RunAbortHandle()

    watchdog_task: asyncio.Task[None] | None = None
    if spec.max_cost_usd is not None and child_abort is not None:
        watchdog_task = asyncio.create_task(
            _watch_subagent_cost(
                event_log=parent.runner.deps.event_log,
                run_id=child_input.run_id,
                max_cost_usd=spec.max_cost_usd,
                abort_handle=child_abort,
                agent_type=spec.agent_type,
            )
        )

    try:
        output = await parent.run(
            child_input, abort_handle=child_abort, tool_gate=tool_gate
        )
    finally:
        if watchdog_task is not None and not watchdog_task.done():
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task

    # Best-effort structured_output extraction: only when caller asked
    # for response_format AND the answer parses as a JSON object.
    structured: dict[str, Any] | None = None
    if spec.response_format is not None and output.answer:
        try:
            candidate = json.loads(output.answer)
            if isinstance(candidate, dict):
                structured = candidate
        except (TypeError, ValueError):
            structured = None

    return SubagentResult(
        child_run_id=output.run_id,
        parent_run_id=parent_run_id,
        agent_type=spec.agent_type,
        status=output.status,
        terminal_reason=output.terminal_reason,
        answer=output.answer,
        structured_output=structured,
        tool_trace=tuple(output.tool_trace),
        usage=output.usage,
        raw_output=output,
    )


async def _watch_subagent_cost(
    *,
    event_log: Any,
    run_id: str,
    max_cost_usd: float,
    abort_handle: RunAbortHandle,
    agent_type: str,
    poll_interval_seconds: float = 0.05,
) -> None:
    """B2.2 background watchdog — poll the child's event log, sum
    ``cost_usd_estimate`` from every ``llm_call_completed`` event,
    fire ``abort_handle`` once the running total reaches
    ``max_cost_usd``.

    Why a watchdog (not a runtime hook)
    -----------------------------------

    The agent runner already polls the abort handle at every step
    boundary, so the cheapest correct integration is to flip the
    SAME handle from outside the run. We don't need new runtime
    surface area: a small async task watching the durable event log
    is enough to enforce the budget without coupling the runner to a
    cost-tracking concern.

    Exit conditions
    ---------------

    * Budget reached → calls ``abort_handle.abort(reason=
      "budget_exceeded")`` and returns. The child's runner detects
      at the next step boundary.
    * Parent cancels the watchdog (run completed / parent aborted)
      → ``asyncio.CancelledError`` propagates; we let it.
    * Event log raises (custom backend disconnect, etc.) → we log
      and return silently. Better to skip enforcement than to crash
      the parent run; the spec's contract is "soft cost ceiling",
      not "guaranteed enforcement".
    """
    after_seq = 0
    accumulated_cost = 0.0
    while not abort_handle.is_aborted:
        try:
            events = event_log.list_for_run(run_id, after_seq=after_seq)
        except (RuntimeError, OSError, ValueError, TypeError):
            logger.warning(
                "subagent cost watchdog: event_log error for run_id=%r; "
                "skipping enforcement",
                run_id,
                exc_info=True,
            )
            return
        for event in events:
            after_seq = max(after_seq, event.seq)
            if event.type != RuntimeEventType.LLM_CALL_COMPLETED:
                continue
            usage = (event.payload or {}).get("usage") or {}
            if not isinstance(usage, dict):
                continue
            cost = usage.get("cost_usd_estimate")
            if not isinstance(cost, (int, float)):
                continue
            accumulated_cost += float(cost)
            if accumulated_cost >= max_cost_usd:
                logger.info(
                    "subagent cost budget exceeded: agent_type=%r run_id=%r "
                    "cost=%.4f budget=%.4f",
                    agent_type,
                    run_id,
                    accumulated_cost,
                    max_cost_usd,
                )
                abort_handle.abort(reason="budget_exceeded")
                return
        await asyncio.sleep(poll_interval_seconds)


__all__ = [
    "SubagentLimits",
    "SubagentOutputPolicy",
    "SubagentResult",
    "SubagentResultArtifacts",
    "SubagentResultIdentity",
    "SubagentResultOutcome",
    "SubagentSpec",
    "SubagentToolPolicy",
    "run_subagent",
]
