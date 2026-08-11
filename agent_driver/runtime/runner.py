"""Durable single-agent runner and compatibility fake runner."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from time import monotonic

from agent_driver.code_agent.backends import create_python_backend
from agent_driver.code_agent.executor import FakeRestrictedCodeExecutor
from agent_driver.context import (
    InMemoryArtifactStore,
    InMemoryContextStore,
    InMemorySessionStore,
)
from agent_driver.contracts.control import LiveMessagePhase
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.llm.providers import LlmProvider
from agent_driver.observability.openinference import (
    SPAN_KIND_AGENT,
    oi_span,
    record_status,
    set_io,
)
from agent_driver.runtime.abort import RunAbortHandle  # noqa: F401
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.lifecycle_hooks import dispatch_error
from agent_driver.runtime.metadata_state import get_loop_control_state
from agent_driver.runtime.single_agent.finalization.output import SingleAgentOutputMixin
from agent_driver.runtime.single_agent.llm_step.completion import AbortRequested
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmGenerationSuperseded,
)
from agent_driver.runtime.single_agent.lifecycle.journal import SingleAgentJournalMixin
from agent_driver.runtime.single_agent.lifecycle.resume import SingleAgentResumeMixin
from agent_driver.runtime.single_agent.lifecycle.steps import SingleAgentStepMixin
from agent_driver.runtime.tool_gate import (  # noqa: F401 (re-exported via runtime/__init__)
    ToolGate,
)

# isort: off
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext as _RunContext,
    RunnerConfig,
    TerminalResult,
)  # noqa: F401

# isort: on
from agent_driver.runtime.single_agent.types import RunnerDeps
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tools import fake_noop_tool_executor
from agent_driver.subagents.mailbox import InMemorySubagentMailboxStore
from agent_driver.subagents.store import InMemorySubagentStore
from agent_driver.tools import register_builtin_tools, register_planning_tool
from agent_driver.contracts.execution_lease import (
    ExecutionLeaseRef,
    ExecutionLeaseRequest,
)
from agent_driver.execution.adapters import BackendCommandRunner, BackendFileIO
from agent_driver.execution.capabilities import resolve_capability_snapshot
from agent_driver.execution.errors import UnsupportedCapabilityError
from agent_driver.execution.lease import ExecutionLeaseManager, LeaseNotUsableError

if TYPE_CHECKING:
    from agent_driver.execution.protocol import ExecutionBackend
from agent_driver.tools.context import (
    capability_snapshot_scope,
    command_runner_scope,
    execution_lease_scope,
    fs_io_scope,
    workspace_backend_scope,
    workspace_cwd_scope,
)
from agent_driver.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class SingleAgentRunner(
    SingleAgentStepMixin,
    SingleAgentResumeMixin,
    SingleAgentOutputMixin,
    SingleAgentJournalMixin,
):
    """Durable single-agent runner with checkpointed step transitions.

    Mixin order matters:
    - `SingleAgentStepMixin` drives the step loop and calls helper hooks.
    - `SingleAgentResumeMixin` initializes context and applies resume actions.
    - `SingleAgentOutputMixin` assembles terminal/paused `AgentRunOutput`.
    - `SingleAgentJournalMixin` provides event emission and checkpoint persistence.
    """

    @staticmethod
    def _build_default_tool_registry(
        *, config: RunnerConfig, python_backend: object | None = None
    ) -> ToolRegistry:
        """Build default tool registry with built-in read/search tools."""
        registry = ToolRegistry()
        register_builtin_tools(
            registry,
            python_backend=python_backend,
            python_settings=config.python_tool,
        )
        register_planning_tool(registry)
        return registry

    def __init__(
        self,
        *,
        provider: LlmProvider,
        checkpoint_store: CheckpointStore,
        event_log: RuntimeEventLog,
        config: RunnerConfig | None = None,
    ) -> None:
        self._config = config or RunnerConfig()
        python_backend = None
        if self._config.python_tool.enabled:
            python_backend = create_python_backend(
                self._config.python_tool.backend,
                session_idle_seconds=self._config.python_tool.session_idle_seconds,
            )
        self._deps = RunnerDeps(
            provider=provider,
            checkpoint_store=checkpoint_store,
            event_log=event_log,
            tool_executor=self._config.tool_executor or fake_noop_tool_executor,
            session_store=self._config.session_store or InMemorySessionStore(),
            artifact_store=self._config.artifact_store or InMemoryArtifactStore(),
            context_store=self._config.context_store or InMemoryContextStore(),
            subagent_store=self._config.subagent_store or InMemorySubagentStore(),
            subagent_mailbox_store=self._config.subagent_mailbox_store
            or InMemorySubagentMailboxStore(),
            code_executor=self._config.code_executor or FakeRestrictedCodeExecutor(),
            tool_registry=self._config.tool_registry
            or self._build_default_tool_registry(
                config=self._config,
                python_backend=python_backend,
            ),
            command_queue_store=self._config.command_queue_store,
            approval_store=getattr(self._config, "approval_store", None),
            abort_store=getattr(self._config, "abort_store", None),
            plan_artifact_store=getattr(self._config, "plan_artifact_store", None),
            python_backend=python_backend,
            lifecycle_hooks=self._build_lifecycle_hooks(),
            fallback_providers=tuple(
                getattr(self._config, "fallback_providers", ()) or ()
            ),
            fallback_models=tuple(
                getattr(self._config, "fallback_models", ()) or ()
            ),
            role_providers=dict(getattr(self._config, "role_providers", {}) or {}),
        )

    def _build_lifecycle_hooks(self) -> tuple:
        """Assemble run lifecycle hooks: memory adapter first, then config hooks.

        The user-facing ``memory_provider`` is translated into a
        ``MemoryLifecycleHook`` here so the step loop only ever sees the generic
        hook seam, not memory-specific wiring.
        """
        hooks = list(getattr(self._config, "lifecycle_hooks", ()) or ())
        memory_provider = getattr(self._config, "memory_provider", None)
        if memory_provider is not None:
            from agent_driver.runtime.single_agent.lifecycle.memory_hook import (
                MemoryLifecycleHook,
            )

            hooks.insert(
                0,
                MemoryLifecycleHook(
                    memory_provider,
                    consolidation_every_n_turns=int(
                        getattr(
                            self._config,
                            "memory_consolidation_every_n_turns",
                            0,
                        )
                        or 0
                    ),
                ),
            )
        # Built-in node-contract hook: inert unless AgentRunInput.node_contract is
        # active, so always-on registration is byte-for-byte safe for other runs.
        from agent_driver.runtime.single_agent.node_contract import (
            NodeContractLifecycleHook,
        )

        hooks.insert(0, NodeContractLifecycleHook())
        return tuple(hooks)

    @property
    def config(self) -> RunnerConfig:
        """Runner configuration (read-only for stage adapters)."""
        return self._config

    @property
    def deps(self) -> RunnerDeps:
        """Runner dependencies (read-only for stage adapters)."""
        return self._deps

    async def run(
        self,
        run_input: AgentRunInput,
        *,
        abort_handle: "RunAbortHandle | None" = None,
        tool_gate: "ToolGate | None" = None,
        execution_backend: "ExecutionBackend | None" = None,
    ) -> AgentRunOutput:
        """Execute deterministic step loop with per-step checkpointing.

        ``abort_handle`` is an optional caller-supplied
        :class:`RunAbortHandle`. When the caller flips it
        (``handle.abort(reason=...)``) the runtime detects it at the
        next step boundary and terminates with ``RunStatus.CANCELLED``
        / ``TerminalReason.CANCELLED_BY_USER``. Subagents spawned via
        :func:`run_subagent` inherit a weak-ref'd child of this handle
        so a single ``.abort()`` cascades through the tree.

        ``tool_gate`` is an optional caller-supplied async per-call
        gate (A0.2). When set, the governed tool executor consults it
        AFTER the static ``ToolPolicyInput`` pass returns ALLOW; the
        gate may flip the decision to DENY (blocked envelope) or ASK
        (operator interrupt). See
        :mod:`agent_driver.runtime.tool_gate` for the contract.
        """
        replayed = self._maybe_replay_prior_result(run_input)
        if replayed is not None:
            return replayed
        context = self._init_context(
            run_input,
            abort_handle=abort_handle,
            tool_gate=tool_gate,
            execution_backend=execution_backend,
        )
        command_store = getattr(self._deps, "command_queue_store", None)
        set_phase = getattr(command_store, "set_run_phase", None)
        if callable(set_phase):
            initial_phase = (
                LiveMessagePhase.TOOL_IN_FLIGHT
                if context.step_name == "tool_stage"
                else LiveMessagePhase.FINALIZING
                if context.step_name == "finalize"
                else LiveMessagePhase.LLM_IN_FLIGHT
            )
            state = set_phase(
                context.run_id,
                initial_phase,
                thread_id=context.run_input.thread_id,
                agent_id=context.run_input.agent_id,
            )
            context.metadata["llm_generation"] = state.llm_generation
        output: AgentRunOutput | None = None
        with oi_span("agent.run", kind=SPAN_KIND_AGENT) as run_span:
            _annotate_run_span_input(run_span, run_input)
            try:
                output = await self._drive_steps(context)
                self._finalize_abort_lifecycle(context, output)
                self._record_approval_result(run_input, output)
                await self._dispatch_run_error(context, output)
                return output
            finally:
                # EPIC-03: authoritative, idempotent lease release/detach on EVERY
                # exit (normal, exception, timeout, cancellation). Safe when no
                # lease was acquired (``output`` may be None here).
                await self._release_execution_lease(context, output)
                _annotate_run_span_output(run_span, output)

    def _maybe_replay_prior_result(
        self, run_input: AgentRunInput
    ) -> AgentRunOutput | None:
        """F2 — return the prior recorded terminal output for a duplicate approve.

        No-op unless ``replay_prior_result`` is enabled, an approval store is
        configured, this is a resume, and the targeted interrupt was already
        consumed with a recorded output. Lets a duplicate approval (HTTP-style
        retry) return the exact prior result instead of re-driving or conflicting.
        """
        store = getattr(self._deps, "approval_store", None)
        if (
            store is None
            or not getattr(self._config, "replay_prior_result", False)
            or run_input.resume is None
            or not run_input.run_id
        ):
            return None
        try:
            outcome = store.get(
                run_id=run_input.run_id,
                interrupt_id=run_input.resume.interrupt_id,
            )
        except Exception:  # pragma: no cover - a ledger read must not break a run
            return None
        if outcome is None or not outcome.prior_result_payload:
            return None
        try:
            return AgentRunOutput.model_validate_json(outcome.prior_result_payload)
        except Exception:  # pragma: no cover - corrupt payload → fall through
            logger.warning("prior result replay payload invalid", exc_info=True)
            return None

    def _record_approval_result(
        self, run_input: AgentRunInput, output: AgentRunOutput
    ) -> None:
        """F2 — persist the terminal output of a consumed approval for replay."""
        store = getattr(self._deps, "approval_store", None)
        if (
            store is None
            or run_input.resume is None
            or not run_input.run_id
            or output.status == RunStatus.PAUSED
        ):
            return
        try:
            store.record_result(
                run_id=run_input.run_id,
                interrupt_id=run_input.resume.interrupt_id,
                result_payload=output.model_dump_json(),
            )
        except Exception:  # pragma: no cover - a ledger write must not break a run
            logger.warning("approval result record failed", exc_info=True)

    def _finalize_abort_lifecycle(
        self, context: _RunContext, output: AgentRunOutput
    ) -> None:
        """U4 A/D — record the truthful durable abort outcome for this run.

        On a terminal output, when an abort-lifecycle store is configured:
        a user-cancelled run is marked observed → cancelled; a run that finished
        while a stop was pending (durable request or a late handle abort) is
        resolved completed_before_cancel. No-op without a store, on a paused
        run, or when no abort was ever in play. A ledger write must never fail a
        run, so errors are swallowed.
        """
        store = getattr(self._deps, "abort_store", None)
        if store is None or output.status == RunStatus.PAUSED:
            return
        handle = context.abort_handle
        aborted = handle is not None and getattr(handle, "is_aborted", False)
        reason = getattr(handle, "reason", None) if handle is not None else None
        try:
            if output.terminal_reason in (
                TerminalReason.CANCELLED_BY_USER,
                TerminalReason.CANCELLATION_FAILED,
            ):
                store.mark_observed(context.run_id, reason=reason)
                store.resolve(context.run_id, cancelled=True)
            elif aborted or store.get(context.run_id) is not None:
                if store.get(context.run_id) is None:
                    store.request_abort(context.run_id, reason=reason)
                store.resolve(context.run_id, cancelled=False)
        except Exception:  # pragma: no cover - telemetry must never break a run
            logger.warning("abort lifecycle store update failed", exc_info=True)

    async def _dispatch_run_error(
        self, context: _RunContext, output: AgentRunOutput
    ) -> None:
        """Notify lifecycle hooks when a run terminated in failure.

        User-cancelled runs are excluded (nothing to self-heal). Hook
        exceptions are swallowed: the run already failed and a recovery hook
        must not mask the original outcome.
        """
        if output.status not in (RunStatus.FAILED, RunStatus.TIMED_OUT):
            return
        hooks = self._deps.lifecycle_hooks
        if not hooks:
            return
        events = self._deps.event_log.list_for_run(context.run_id)
        try:
            await dispatch_error(hooks, context, output=output, events=events)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("run lifecycle on_error hook failed")

    def _resolved_backend(self, context: _RunContext) -> "ExecutionBackend | None":
        return context.execution_backend or getattr(
            self._config, "execution_backend", None
        )

    def _lease_attach_ref(self, context: _RunContext) -> ExecutionLeaseRef | None:
        """Parse a persisted/host-supplied lease reference from run metadata.

        Covers both resume (the runner wrote it after acquire) and a host-owned
        attach (``app_metadata["execution_lease_ref"]``). A malformed value is
        ignored rather than crashing the run.
        """
        raw = context.metadata.get("execution_lease_ref")
        if not isinstance(raw, dict):
            return None
        try:
            return ExecutionLeaseRef.model_validate(raw)
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return None

    async def _setup_execution_lease(
        self, context: _RunContext, backend: object, stack: "contextlib.ExitStack"
    ) -> AgentRunOutput | None:
        """Acquire or attach the run's workspace lease, or return a terminal
        FAILED output on fail-closed. Returns ``None`` when no lease is needed."""
        # Subagent lease policy (EPIC-03 scenario 11): the DEFAULT is ISOLATE —
        # a subagent child neither acquires nor attaches an execution lease. This
        # prevents an accidental double-acquire and guarantees a child can never
        # release/detach the parent's lease (the parent's lease is fully isolated
        # from subagent activity). A child run is identified by the parent-handoff
        # metadata the subagent executor stamps.
        if context.metadata.get("parent_run_id") or context.metadata.get(
            "subagent_group_id"
        ):
            return None
        attach_ref = self._lease_attach_ref(context)
        ownership = getattr(self._config, "execution_lease_ownership", None)
        if attach_ref is None and ownership is None:
            return None  # no lease requested — stateless backend use
        manager = ExecutionLeaseManager()
        try:
            if attach_ref is not None:
                lease = await manager.attach_by_ref(backend, attach_ref)
            else:
                request = ExecutionLeaseRequest(
                    request_id=f"{context.run_id}:{context.attempt_id}:lease",
                    backend_id=getattr(backend, "backend_id", "unknown"),
                    ownership=ownership,
                    workspace_id=context.run_input.workspace_id,
                )
                lease = await manager.acquire_or_attach(backend, request)
        except (LeaseNotUsableError, UnsupportedCapabilityError) as exc:
            # Fail closed: a requested lease could not be secured. Never fall back
            # to local execution — terminate with a typed FAILED outcome.
            context.metadata["execution_lease_failure"] = {
                "reason_code": exc.code,
                "message": exc.message,
            }
            self._emit(
                EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_FAILED,
                    payload={"reason": "execution_lease_unavailable"},
                )
            )
            return self._build_output(
                context,
                TerminalResult(
                    status=RunStatus.FAILED, reason=TerminalReason.RUNTIME_ERROR
                ),
            )
        context.execution_lease_manager = manager
        context.metadata["execution_lease_ref"] = lease.ref.model_dump(mode="json")
        stack.enter_context(execution_lease_scope(lease))
        return None

    async def _release_execution_lease(
        self, context: _RunContext, output: AgentRunOutput | None
    ) -> None:
        """Idempotently release (runtime-owned) or detach (host-owned) the lease
        on TERMINAL exits. A PAUSED run RETAINS its lease (explicit policy, not
        accidental retention): the safe reference is already persisted to
        metadata so resume re-attaches. Called from the outer ``finally``; never
        raises. ``output`` is None when the run raised before producing one."""
        manager = getattr(context, "execution_lease_manager", None)
        if manager is None:
            return
        if output is not None and output.status is RunStatus.PAUSED:
            # keep the lease alive across the interrupt; surface its receipts
            self._record_lease_receipts(context, manager)
            return
        await manager.close(self._resolved_backend(context))
        self._record_lease_receipts(context, manager)

    @staticmethod
    def _record_lease_receipts(context: _RunContext, manager: object) -> None:
        """Surface lease phase timings (queue/acquire/ready/release) into run
        metadata for independent observation (EPIC-03 scenario 10)."""
        receipts = getattr(manager, "receipts", ())
        if receipts:
            context.metadata["execution_lease_receipts"] = [
                r.model_dump(mode="json") for r in receipts
            ]

    async def _drive_steps(self, context: _RunContext) -> AgentRunOutput:
        """Drive the deterministic step loop to a terminal output.

        Extracted from :meth:`run` so the run-level OpenInference AGENT
        span wraps the whole loop (including the early terminal/timeout
        returns) and becomes the native trace root that nested
        LLM/TOOL/subagent spans parent to (Workstream B). A subagent that
        re-enters :meth:`run` synchronously opens its own AGENT span under
        this one, giving Phoenix native subagent grouping.
        """
        # A per-run backend (host-injected via run(execution_backend=...)) wins
        # over the config default; None keeps the local subprocess + disk path.
        backend = context.execution_backend or getattr(
            self._config, "execution_backend", None
        )
        # EPIC-02: one capability handshake per run, fail-safe to all-UNKNOWN.
        # The same snapshot governs the pre-model tool filter and the
        # pre-dispatch governed re-check, so both see identical truth.
        capability_snapshot = (
            await resolve_capability_snapshot(backend) if backend is not None else None
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(workspace_cwd_scope(_pick_workspace_cwd(context)))
            if backend is not None:
                # Route the built-in bash/read/write byte transfer through the
                # injected backend WITHOUT touching the tools: install the
                # adapters into the existing run-scoped seams. Path resolution /
                # jailing and all governance still run above dispatch.
                stack.enter_context(command_runner_scope(BackendCommandRunner(backend)))
                stack.enter_context(fs_io_scope(BackendFileIO(backend)))
                stack.enter_context(capability_snapshot_scope(capability_snapshot))
                # EPIC-03 WP-C: route workspace enumeration/search/stat/delete to
                # the backend when it supports those operations (no local disk).
                if callable(getattr(backend, "glob", None)) and callable(
                    getattr(backend, "grep", None)
                ):
                    stack.enter_context(workspace_backend_scope(backend))
                # EPIC-03: acquire/attach the workspace lease once, reuse across
                # the loop. On fail-closed it returns a terminal FAILED output
                # (never a silent local fallback after a lease was requested).
                lease_terminal = await self._setup_execution_lease(
                    context, backend, stack
                )
                if lease_terminal is not None:
                    return lease_terminal
            while context.step_name != "done":
                terminal = self._terminal_from_limits(context)
                if terminal is not None:
                    event_type = (
                        RuntimeEventType.RUN_CANCELLED
                        if terminal.reason == TerminalReason.CANCELLED_BY_USER
                        else RuntimeEventType.RUN_FAILED
                    )
                    self._emit(
                        EventSpec(
                            run_id=context.run_id,
                            attempt_id=context.attempt_id,
                            event_type=event_type,
                            payload={"reason": terminal.reason.value},
                        )
                    )
                    self._emit_observe_policy_decisions(
                        context,
                        trigger="terminal_limit",
                    )
                    return self._build_output(context, terminal)
                timeout, guard_kind = _step_timeout_seconds(
                    context,
                    hard_max_seconds=getattr(
                        self._config, "default_hard_max_seconds", None
                    ),
                    idle_timeout_seconds=getattr(
                        self._config, "default_idle_timeout_seconds", None
                    ),
                )
                try:
                    if timeout is None:
                        result = await self._execute_step(context)
                    else:
                        result = await asyncio.wait_for(
                            self._execute_step(context),
                            timeout=max(0.001, timeout),
                        )
                except TimeoutError:
                    # U4 — if a stop was requested but the step blew the
                    # wall-clock guard, a handler ignored cooperative
                    # cancellation: surface CANCELLATION_FAILED (an enforced
                    # stop) instead of a plain DEADLINE_EXCEEDED, so the terminal
                    # is truthful about why the run ended.
                    handle = context.abort_handle
                    aborted = handle is not None and getattr(
                        handle, "is_aborted", False
                    )
                    terminal = TerminalResult(
                        status=RunStatus.CANCELLED if aborted else RunStatus.TIMED_OUT,
                        reason=TerminalReason.CANCELLATION_FAILED
                        if aborted
                        else TerminalReason.DEADLINE_EXCEEDED,
                    )
                    context.metadata["wall_clock_guard"] = guard_kind
                    self._emit(
                        EventSpec(
                            run_id=context.run_id,
                            attempt_id=context.attempt_id,
                            event_type=RuntimeEventType.RUN_FAILED,
                            payload={
                                "reason": terminal.reason.value,
                                "wall_clock_guard": guard_kind,
                            },
                        )
                    )
                    self._emit_observe_policy_decisions(
                        context,
                        trigger="terminal_timeout",
                    )
                    return self._build_output(context, terminal)
                except AbortRequested:
                    # U4 — a stop was observed mid-LLM-call and the in-flight
                    # request was cancelled promptly. Map the typed signal
                    # explicitly to a CANCELLED terminal (never MODEL_ERROR).
                    terminal = TerminalResult(
                        status=RunStatus.CANCELLED,
                        reason=TerminalReason.CANCELLED_BY_USER,
                    )
                    self._emit(
                        EventSpec(
                            run_id=context.run_id,
                            attempt_id=context.attempt_id,
                            event_type=RuntimeEventType.RUN_CANCELLED,
                            payload={"reason": terminal.reason.value},
                        )
                    )
                    self._emit_observe_policy_decisions(
                        context,
                        trigger="terminal_abort",
                    )
                    return self._build_output(context, terminal)
                except LlmGenerationSuperseded:
                    # Another worker/redirect generation now owns this run. The
                    # stale attempt may record that it was fenced, but must not
                    # checkpoint, dispatch tools, or commit a terminal outcome.
                    self._emit(
                        EventSpec(
                            run_id=context.run_id,
                            attempt_id=context.attempt_id,
                            event_type=RuntimeEventType.RESULT_FENCED,
                            payload={
                                "reason": "llm_generation_superseded",
                                "llm_generation": context.metadata.get(
                                    "llm_generation", 0
                                ),
                            },
                        )
                    )
                    raise
                context.step_name = result.next_step
            payload = get_loop_control_state(context).terminal_output()
            if not isinstance(payload, dict):
                raise RuntimeExecutionError("Missing terminal output metadata")
            return AgentRunOutput.model_validate(payload)


def _pick_workspace_cwd(context: _RunContext):
    """Resolve run-scoped workspace cwd from metadata hints."""
    loop_state = get_loop_control_state(context)
    workspace_raw = loop_state.workspace_cwd()
    if workspace_raw is not None:
        return Path(workspace_raw).expanduser().resolve()
    sandbox_raw = loop_state.eval_sandbox_dir()
    if sandbox_raw is not None:
        return Path(sandbox_raw).expanduser().resolve()
    return None


def _step_timeout_seconds(
    context: _RunContext,
    *,
    hard_max_seconds: float | None,
    idle_timeout_seconds: float | None,
) -> tuple[float | None, str]:
    """Effective per-step timeout and which wall-clock guard produced it.

    Three independent sources (epic 019; reference: openclaude QueryGuard idle
    5m / hard-max 30m as separate loop fuses):

    - per-run ``deadline_seconds`` (explicit caller budget — remaining time);
    - config ``default_hard_max_seconds`` (run-level safety net — remaining time);
    - config ``default_idle_timeout_seconds`` (bounds ONE step's await: a wedged
      tool/provider that produces no step progress for this long is cut, even
      when the run-level budgets still have plenty of time).

    The tightest bound wins; the guard kind is surfaced in the terminal payload
    so operators can tell an explicit deadline from a safety-net trip.
    """
    elapsed = monotonic() - context.started_at
    candidates: list[tuple[float, str]] = []
    deadline = context.run_input.deadline_seconds
    if deadline is not None:
        candidates.append((float(deadline) - elapsed, "run_deadline"))
    if hard_max_seconds is not None:
        candidates.append((float(hard_max_seconds) - elapsed, "hard_max"))
    if idle_timeout_seconds is not None:
        candidates.append((float(idle_timeout_seconds), "step_idle"))
    if not candidates:
        return None, ""
    timeout, kind = min(candidates, key=lambda item: item[0])
    return timeout, kind


def _annotate_run_span_input(span: object, run_input: AgentRunInput) -> None:
    """Seed the run AGENT span with agent identity + the user turn.

    No-op/never-raises — telemetry must never break a run.
    """
    if span is None:
        return
    try:
        profile = getattr(run_input.agent_profile, "value", run_input.agent_profile)
        for key, value in (
            ("agent.id", run_input.agent_id),
            ("agent.profile", str(profile) if profile is not None else None),
            ("llm.model_role", run_input.model_role),
            ("graph.preset", run_input.graph_preset),
        ):
            if value is not None:
                span.set_attribute(key, value)
        if run_input.input:
            set_io(span, input=run_input.input)
        elif run_input.messages:
            set_io(
                span,
                input=[m.model_dump(mode="json") for m in run_input.messages],
            )
    except Exception:  # telemetry must never break a run
        pass


def _annotate_run_span_output(span: object, output: AgentRunOutput | None) -> None:
    """Record the final answer + ERROR status on the run AGENT span."""
    if span is None or output is None:
        return
    try:
        if output.answer is not None:
            set_io(span, output=output.answer)
        ok = output.status in (RunStatus.COMPLETED, RunStatus.PAUSED)
        description = None
        if not ok:
            description = f"run ended {output.status.value}"
            if output.terminal_reason is not None:
                description = f"{description}: {output.terminal_reason.value}"
        record_status(span, ok=ok, description=description)
    except Exception:  # telemetry must never break a run
        pass


class FakeSingleStepRunner(SingleAgentRunner):
    """Backward-compatible alias for prior fake one-step runtime runner."""
