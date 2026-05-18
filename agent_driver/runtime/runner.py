"""Durable single-agent runner and compatibility fake runner."""

from __future__ import annotations

from agent_driver.contracts.enums import RuntimeEventType, TerminalReason
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.single_agent.journal import SingleAgentJournalMixin
from agent_driver.runtime.single_agent.output import SingleAgentOutputMixin
from agent_driver.runtime.single_agent.resume import SingleAgentResumeMixin
from agent_driver.runtime.single_agent.steps import SingleAgentStepMixin

# isort: off
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    PendingInterruptState as _PendingInterruptState,
    RunContext as _RunContext,
    RunnerConfig,
)  # noqa: F401

# isort: on
from agent_driver.runtime.single_agent.types import RunnerDeps
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tools import fake_noop_tool_executor


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

    def __init__(
        self,
        *,
        provider: LlmProvider,
        checkpoint_store: CheckpointStore,
        event_log: RuntimeEventLog,
        config: RunnerConfig | None = None,
    ) -> None:
        self._config = config or RunnerConfig()
        self._deps = RunnerDeps(
            provider=provider,
            checkpoint_store=checkpoint_store,
            event_log=event_log,
            tool_executor=self._config.tool_executor or fake_noop_tool_executor,
        )

    async def run(self, run_input: AgentRunInput) -> AgentRunOutput:
        """Execute deterministic step loop with per-step checkpointing."""
        context = self._init_context(run_input)
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
                return self._build_output(context, terminal)
            result = await self._execute_step(context)
            context.step_name = result.next_step
        payload = context.metadata.get("terminal_output")
        if not isinstance(payload, dict):
            raise RuntimeExecutionError("Missing terminal output metadata")
        return AgentRunOutput.model_validate(payload)


class FakeSingleStepRunner(SingleAgentRunner):
    """Backward-compatible alias for prior fake one-step runtime runner."""
