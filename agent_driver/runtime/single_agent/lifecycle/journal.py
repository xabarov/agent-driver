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
from agent_driver.runtime.metadata_state import get_cost_runtime_state
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
    TerminalResult,
)
from agent_driver.runtime.state import RuntimeState


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
        max_steps = context.run_input.max_steps
        if max_steps is not None and context.llm_step_count >= max_steps:
            return TerminalResult(
                status=RunStatus.FAILED,
                reason=TerminalReason.MAX_STEPS_EXCEEDED,
            )
        max_tool_calls = context.run_input.max_tool_calls
        if max_tool_calls is not None and context.tool_calls > max_tool_calls:
            return TerminalResult(
                status=RunStatus.FAILED,
                reason=TerminalReason.TOOL_POLICY_DENIED,
            )
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


__all__ = ["SingleAgentJournalMixin"]
