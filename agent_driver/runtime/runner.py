"""Durable single-agent runner and compatibility fake runner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic

from agent_driver.code_agent.backends import create_python_backend
from agent_driver.code_agent.executor import FakeRestrictedCodeExecutor
from agent_driver.context import (
    InMemoryArtifactStore,
    InMemoryContextStore,
    InMemorySessionStore,
)
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.errors import RuntimeExecutionError
from agent_driver.runtime.abort import RunAbortHandle  # noqa: F401
from agent_driver.runtime.metadata_state import get_loop_control_state
from agent_driver.runtime.tool_gate import ToolGate  # noqa: F401 (re-exported via runtime/__init__)
from agent_driver.runtime.single_agent.journal import SingleAgentJournalMixin
from agent_driver.runtime.single_agent.finalization.output import SingleAgentOutputMixin
from agent_driver.runtime.single_agent.resume import SingleAgentResumeMixin
from agent_driver.runtime.single_agent.steps import SingleAgentStepMixin

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
from agent_driver.tools.context import workspace_cwd_scope
from agent_driver.tools.registry import ToolRegistry


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
            python_backend=python_backend,
        )

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
        context = self._init_context(
            run_input, abort_handle=abort_handle, tool_gate=tool_gate
        )
        with workspace_cwd_scope(_pick_workspace_cwd(context)):
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
                timeout = _remaining_deadline_seconds(context)
                try:
                    if timeout is None:
                        result = await self._execute_step(context)
                    else:
                        result = await asyncio.wait_for(
                            self._execute_step(context),
                            timeout=max(0.001, timeout),
                        )
                except TimeoutError:
                    terminal = TerminalResult(
                        status=RunStatus.TIMED_OUT,
                        reason=TerminalReason.DEADLINE_EXCEEDED,
                    )
                    self._emit(
                        EventSpec(
                            run_id=context.run_id,
                            attempt_id=context.attempt_id,
                            event_type=RuntimeEventType.RUN_FAILED,
                            payload={"reason": terminal.reason.value},
                        )
                    )
                    return self._build_output(context, terminal)
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


def _remaining_deadline_seconds(context: _RunContext) -> float | None:
    deadline = context.run_input.deadline_seconds
    if deadline is None:
        return None
    return float(deadline) - (monotonic() - context.started_at)




class FakeSingleStepRunner(SingleAgentRunner):
    """Backward-compatible alias for prior fake one-step runtime runner."""
