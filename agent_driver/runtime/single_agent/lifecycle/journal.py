"""Event emission, checkpoints, run limits for SingleAgentRunner."""

from __future__ import annotations

from time import monotonic
from typing import cast

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.events import (
    RuntimeEvent,
    RuntimeEventContext,
    new_runtime_event,
)
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.metadata_state import (
    get_cost_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
    TerminalResult,
)
from agent_driver.runtime.state import RuntimeState


# How many extra LLM steps the forced-final synthesis window may take after a
# soft-budget grace is granted before the run is hard-terminated. One step is
# the synthesis call itself; the small margin tolerates a finalize step or a
# single tools-disabled retry without reopening the runaway.
_GRACE_EXTRA_LLM_STEPS = 2


class SingleAgentJournalMixin:  # pylint: disable=too-few-public-methods
    """Mixin: event log, checkpoint persistence, cancellation/deadline limits."""

    _config: RunnerConfig
    _deps: RunnerDeps

    @property
    def graph_id(self) -> str:
        """Configured graph id for checkpoints."""
        return self._config.graph_id

    def _next_seq(self, run_id: str) -> int:
        events = cast(list[RuntimeEvent], self._deps.event_log.list_for_run(run_id))
        return (max(event.seq for event in events) + 1) if events else 1

    def _emit(self, spec: EventSpec) -> RuntimeEvent:
        event = new_runtime_event(
            event_type=spec.event_type,
            context=RuntimeEventContext(
                run_id=spec.run_id,
                attempt_id=spec.attempt_id,
                seq=self._next_seq(spec.run_id),
            ),
            options={"payload": spec.payload or {}},
        )
        self._deps.event_log.append(event)
        return event

    def _save_checkpoint(
        self,
        context: RunContext,
        *,
        latest_output: AgentRunOutput | None,
        node_id: str,
    ) -> CheckpointRef:
        state = RuntimeState(
            run_input=context.run_input,
            latest_output=latest_output,
            events=self._deps.event_log.list_for_run(context.run_id),
            checkpoint=context.prior_checkpoint,
            metadata=context.metadata,
        )
        ref = cast(
            CheckpointRef,
            self._deps.checkpoint_store.save(
                graph_id=self.graph_id,
                node_id=node_id,
                state=state,
            ),
        )
        context.prior_checkpoint = ref
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.CHECKPOINT_SAVED,
                payload={"checkpoint_id": ref.checkpoint_id},
            )
        )
        return ref

    def _maybe_fail_after_step(self, step_name: str) -> None:
        if self._config.fail_after_step == step_name:
            raise RuntimeExecutionError(f"Injected failure after step '{step_name}'")

    def _terminal_from_limits(self, context: RunContext) -> TerminalResult | None:
        # Two complementary cancellation seams. The probe is config-level
        # and exists for callers that wire a closure / global flag; the
        # abort_handle is an object-shaped, hierarchical signal that
        # subagents inherit via ``parent.child()``. Either fires the
        # same terminal — first one wins.
        probe = self._config.cancellation_probe
        if probe is not None and probe():
            return TerminalResult(
                status=RunStatus.CANCELLED,
                reason=TerminalReason.CANCELLED_BY_USER,
            )
        handle = context.abort_handle
        if handle is not None and handle.is_aborted:
            return TerminalResult(
                status=RunStatus.CANCELLED,
                reason=TerminalReason.CANCELLED_BY_USER,
            )
        deadline = context.run_input.deadline_seconds
        if deadline is not None and (monotonic() - context.started_at) > deadline:
            return TerminalResult(
                status=RunStatus.TIMED_OUT,
                reason=TerminalReason.DEADLINE_EXCEEDED,
            )
        # Per-run max_steps wins; otherwise fall back to the config-level
        # defensive backstop so a run that never reaches a final answer can't
        # loop forever (config default_max_steps=None opts back into unbounded).
        max_steps = context.run_input.max_steps
        if max_steps is None:
            max_steps = self._config.default_max_steps
        if max_steps is not None and context.llm_step_count >= max_steps:
            return self._soft_budget_terminal(
                context, TerminalReason.MAX_STEPS_EXCEEDED
            )
        max_tool_calls = context.run_input.max_tool_calls
        if max_tool_calls is not None and context.tool_calls > max_tool_calls:
            return self._soft_budget_terminal(
                context, TerminalReason.TOOL_POLICY_DENIED
            )
        # A cost ceiling is a hard money stop: do NOT grant a grace synthesis
        # turn (that would spend one more call past the budget). Steps/tool-call
        # exhaustion is "ran out of moves" — those get the grace window above.
        cost_budget = context.run_input.cost_budget_usd
        if (
            cost_budget is not None
            and get_cost_runtime_state(context).total_cost_usd() > cost_budget
        ):
            return TerminalResult(
                status=RunStatus.FAILED,
                reason=TerminalReason.BUDGET_EXCEEDED,
            )
        return None

    def _soft_budget_terminal(
        self, context: RunContext, reason: TerminalReason
    ) -> TerminalResult | None:
        """Resolve a soft-budget exhaustion (steps / tool-calls / cost).

        Instead of immediately returning a hard FAILED with whatever (usually
        empty) answer the run has, grant a short forced-final synthesis window:
        set ``force_final_answer`` so the next LLM step runs tools-disabled and
        the model produces a best-effort answer from the context it already
        gathered (the "grace call" both reference runtimes ship — hermes' budget
        grace, openclaude's continuation nudge). The loop then exits naturally
        when that forced sequence yields a final answer.

        The window is bounded by ``_GRACE_EXTRA_LLM_STEPS`` additional LLM steps
        after the grant, so a model that keeps calling tools through the grace
        turn (ignoring tools-disabled) still terminates deterministically rather
        than reopening the runaway. Grace is preserved as the *original* budget
        reason on the eventual hard terminal.
        """
        hard = TerminalResult(status=RunStatus.FAILED, reason=reason)
        if not self._config.budget_grace_enabled:
            return hard
        granted_at = context.metadata.get("budget_grace_granted_at_step")
        if granted_at is None:
            # First exhaustion: open the grace window and force a final answer.
            context.metadata["budget_grace_granted_at_step"] = context.llm_step_count
            context.metadata["budget_grace_reason"] = reason.value
            get_tool_loop_state(context).ensure_force_final_answer(
                reason=f"budget_exhausted:{reason.value}"
            )
            return None
        # Grace already open: let the forced-final sequence finish, but only for
        # a bounded number of extra LLM steps, then hard-terminate.
        if context.llm_step_count - int(granted_at) <= _GRACE_EXTRA_LLM_STEPS:
            return None
        original = context.metadata.get("budget_grace_reason")
        if isinstance(original, str):
            try:
                hard = TerminalResult(
                    status=RunStatus.FAILED, reason=TerminalReason(original)
                )
            except ValueError:
                pass
        return hard


__all__ = ["SingleAgentJournalMixin"]
