"""Durable single-agent runner and compatibility fake runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.events import (
    RuntimeEvent,
    RuntimeEventContext,
    new_runtime_event,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.tools import ToolTrace
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.errors import MissingCheckpointError, RuntimeExecutionError
from agent_driver.runtime.state import RuntimeState
from agent_driver.runtime.storage import (
    CheckpointRecord,
    CheckpointStore,
    RuntimeEventLog,
)
from agent_driver.runtime.tools import (
    ToolExecutionResult,
    ToolExecutor,
    fake_noop_tool_executor,
)


@dataclass(slots=True)
class RunnerConfig:
    """Configuration for durable single-agent runtime runner."""

    graph_id: str = "single_agent_runtime"
    cancellation_probe: Callable[[], bool] | None = None
    fail_after_step: str | None = None
    tool_executor: ToolExecutor | None = None


@dataclass(slots=True)
class RuntimeStepResult:
    """Internal step transition result."""

    next_step: str


@dataclass(slots=True)
class _RunContext:
    run_input: AgentRunInput
    identifiers: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)
    llm_response: LlmResponse | None = None
    prior_checkpoint: CheckpointRef | None = None
    started_at: float = field(default_factory=monotonic)

    @property
    def run_id(self) -> str:
        """Current run identifier."""
        return self.identifiers["run_id"]

    @property
    def attempt_id(self) -> str:
        """Current attempt identifier."""
        return self.identifiers["attempt_id"]

    @property
    def step_name(self) -> str:
        """Current step pointer in deterministic loop."""
        return str(self.metadata.get("next_step", "run_started"))

    @step_name.setter
    def step_name(self, value: str) -> None:
        self.metadata["next_step"] = value

    @property
    def step_count(self) -> int:
        """Executed transition count in current run."""
        return int(self.metadata.get("step_count", 0))

    @step_count.setter
    def step_count(self, value: int) -> None:
        self.metadata["step_count"] = value

    @property
    def tool_calls(self) -> int:
        """Accumulated tool-call count across tool stages."""
        return int(self.metadata.get("tool_calls", 0))

    @tool_calls.setter
    def tool_calls(self, value: int) -> None:
        self.metadata["tool_calls"] = value


@dataclass(frozen=True, slots=True)
class _EventSpec:
    run_id: str
    attempt_id: str
    event_type: RuntimeEventType
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _TerminalResult:
    status: RunStatus
    reason: TerminalReason


@dataclass(frozen=True, slots=True)
class _RunnerDeps:
    provider: LlmProvider
    checkpoint_store: CheckpointStore
    event_log: RuntimeEventLog
    tool_executor: ToolExecutor


class SingleAgentRunner:
    """Durable single-agent runner with checkpointed step transitions."""

    def __init__(
        self,
        *,
        provider: LlmProvider,
        checkpoint_store: CheckpointStore,
        event_log: RuntimeEventLog,
        config: RunnerConfig | None = None,
    ) -> None:
        self._config = config or RunnerConfig()
        self._deps = _RunnerDeps(
            provider=provider,
            checkpoint_store=checkpoint_store,
            event_log=event_log,
            tool_executor=self._config.tool_executor or fake_noop_tool_executor,
        )

    @property
    def graph_id(self) -> str:
        """Expose configured graph id for diagnostics."""
        return self._config.graph_id

    def _next_seq(self, run_id: str) -> int:
        events = cast(list[RuntimeEvent], self._deps.event_log.list_for_run(run_id))
        return (max(event.seq for event in events) + 1) if events else 1

    def _emit(self, spec: _EventSpec) -> RuntimeEvent:
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

    def _resolve_resume_checkpoint(self, run_input: AgentRunInput):
        if run_input.resume is None:
            return None
        checkpoint_row = cast(
            CheckpointRecord | None,
            self._deps.checkpoint_store.load(run_input.resume.interrupt_id),
        )
        if checkpoint_row is None:
            raise MissingCheckpointError(
                f"Checkpoint '{run_input.resume.interrupt_id}' not found"
            )
        return checkpoint_row

    def _init_context(self, run_input: AgentRunInput) -> _RunContext:
        checkpoint_row = self._resolve_resume_checkpoint(run_input)
        if checkpoint_row is None:
            run_id = run_input.run_id or f"run_{uuid4().hex}"
            return _RunContext(
                run_input=run_input.model_copy(update={"run_id": run_id}),
                identifiers={
                    "run_id": run_id,
                    "attempt_id": f"attempt_{uuid4().hex[:8]}",
                },
                metadata={"next_step": "run_started", "step_count": 0, "tool_calls": 0},
            )
        metadata = dict(checkpoint_row.state.metadata)
        context = _RunContext(
            run_input=run_input.model_copy(
                update={"run_id": checkpoint_row.ref.run_id}
            ),
            identifiers={
                "run_id": checkpoint_row.ref.run_id,
                "attempt_id": checkpoint_row.ref.attempt_id,
            },
            metadata=metadata,
            prior_checkpoint=checkpoint_row.ref,
            llm_response=(
                LlmResponse.model_validate(metadata["last_llm_response"])
                if isinstance(metadata.get("last_llm_response"), dict)
                else None
            ),
        )
        resume = run_input.resume
        if resume is not None:
            self._emit(
                _EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_RESUMED,
                    payload={
                        "interrupt_id": resume.interrupt_id,
                        "action": resume.action.value,
                    },
                )
            )
        return context

    def _save_checkpoint(
        self,
        context: _RunContext,
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
            _EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.CHECKPOINT_SAVED,
                payload={"checkpoint_id": ref.checkpoint_id},
            )
        )
        return ref

    def _build_output(
        self,
        context: _RunContext,
        terminal: _TerminalResult,
    ) -> AgentRunOutput:
        answer = context.llm_response.message.content if context.llm_response else None
        usage = context.llm_response.usage if context.llm_response else None
        messages = [ChatMessage(role="assistant", content=answer)] if answer else []
        tool_trace_payload = context.metadata.get("tool_trace", [])
        tool_trace = []
        if isinstance(tool_trace_payload, list):
            tool_trace = [
                ToolTrace.model_validate(item)
                for item in tool_trace_payload
                if isinstance(item, dict)
            ]
        return AgentRunOutput(
            run_id=context.run_id,
            attempt_id=context.attempt_id,
            thread_id=context.run_input.thread_id,
            status=terminal.status,
            answer=answer,
            messages=messages,
            events=self._deps.event_log.list_for_run(context.run_id),
            tool_trace=tool_trace,
            usage=usage,
            interrupt=context.metadata.get("interrupt_payload"),
            terminal_reason=terminal.reason,
            metadata={
                "graph_id": self.graph_id,
                "tool_results": context.metadata.get("tool_results", []),
            },
        )

    def _maybe_fail_after_step(self, step_name: str) -> None:
        if self._config.fail_after_step == step_name:
            raise RuntimeExecutionError(f"Injected failure after step '{step_name}'")

    def _terminal_from_limits(self, context: _RunContext) -> _TerminalResult | None:
        probe = self._config.cancellation_probe
        if probe is not None and probe():
            return _TerminalResult(
                status=RunStatus.CANCELLED,
                reason=TerminalReason.CANCELLED_BY_USER,
            )
        deadline = context.run_input.deadline_seconds
        if deadline is not None and (monotonic() - context.started_at) > deadline:
            return _TerminalResult(
                status=RunStatus.TIMED_OUT,
                reason=TerminalReason.DEADLINE_EXCEEDED,
            )
        max_steps = context.run_input.max_steps
        if max_steps is not None and context.step_count >= max_steps:
            return _TerminalResult(
                status=RunStatus.FAILED,
                reason=TerminalReason.MAX_STEPS_EXCEEDED,
            )
        max_tool_calls = context.run_input.max_tool_calls
        if max_tool_calls is not None and context.tool_calls > max_tool_calls:
            return _TerminalResult(
                status=RunStatus.FAILED,
                reason=TerminalReason.TOOL_POLICY_DENIED,
            )
        return None

    async def _execute_run_started(self, context: _RunContext) -> RuntimeStepResult:
        self._emit(
            _EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_STARTED,
                payload={"agent_id": context.run_input.agent_id},
            )
        )
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "llm_call",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            }
        )
        self._save_checkpoint(context, latest_output=None, node_id="run_started")
        self._maybe_fail_after_step("run_started")
        return RuntimeStepResult(next_step="llm_call")

    async def _execute_llm_call(self, context: _RunContext) -> RuntimeStepResult:
        self._emit(
            _EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.LLM_CALL_STARTED,
                payload={"provider": self._deps.provider.name},
            )
        )
        prompt = context.run_input.input or (
            context.run_input.messages[-1].content if context.run_input.messages else ""
        )
        try:
            request_metadata = dict(context.run_input.tool_policy.metadata)
            forced_model = request_metadata.pop("forced_model", None)
            context.llm_response = await self._deps.provider.complete(
                LlmRequest(
                    messages=[ChatMessage(role="user", content=prompt)],
                    model_role=context.run_input.model_role,
                    model=forced_model if isinstance(forced_model, str) else None,
                    stream=False,
                    metadata=request_metadata,
                )
            )
        except (RuntimeError, ValueError) as exc:
            self._emit(
                _EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.RUN_FAILED,
                    payload={"reason": TerminalReason.MODEL_ERROR.value},
                )
            )
            raise RuntimeExecutionError("LLM completion failed") from exc
        self._emit(
            _EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.LLM_CALL_COMPLETED,
                payload={
                    "provider": context.llm_response.provider,
                    "model": context.llm_response.model,
                    "finish_reason": context.llm_response.finish_reason.value,
                },
            )
        )
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "tool_stage",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
                "last_llm_response": context.llm_response.model_dump(mode="json"),
            }
        )
        self._save_checkpoint(context, latest_output=None, node_id="llm_call")
        self._maybe_fail_after_step("llm_call")
        return RuntimeStepResult(next_step="tool_stage")

    async def _execute_tool_stage(self, context: _RunContext) -> RuntimeStepResult:
        if context.llm_response is None:
            raise RuntimeExecutionError("Missing LLM response before tool stage")
        result: ToolExecutionResult = await self._deps.tool_executor(
            context.run_input, context.llm_response
        )
        context.tool_calls += len(result.traces)
        context.metadata["tool_trace"] = [
            trace.model_dump(mode="json") for trace in result.traces
        ]
        context.metadata["tool_results"] = [
            item.model_dump(mode="json") for item in result.envelopes
        ]
        if result.interrupt is not None:
            context.metadata["interrupt_payload"] = result.interrupt.model_dump(
                mode="json"
            )
            context.metadata.update(
                {
                    "next_step": "done",
                    "step_count": context.step_count + 1,
                    "tool_calls": context.tool_calls,
                }
            )
            self._emit(
                _EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.INTERRUPT_REQUESTED,
                    payload={"reason": result.interrupt.reason.value},
                )
            )
            paused_output = AgentRunOutput(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                thread_id=context.run_input.thread_id,
                status=RunStatus.PAUSED,
                events=self._deps.event_log.list_for_run(context.run_id),
                tool_trace=result.traces,
                interrupt=result.interrupt,
                metadata={
                    "graph_id": self.graph_id,
                    "tool_results": context.metadata.get("tool_results", []),
                },
            )
            context.metadata["terminal_output"] = paused_output.model_dump(mode="json")
            self._save_checkpoint(
                context, latest_output=paused_output, node_id="tool_stage"
            )
            return RuntimeStepResult(next_step="done")
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "finalize",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            }
        )
        self._save_checkpoint(context, latest_output=None, node_id="tool_stage")
        if result.traces:
            self._emit(
                _EventSpec(
                    run_id=context.run_id,
                    attempt_id=context.attempt_id,
                    event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
                    payload={
                        "tool_calls": len(result.traces),
                        "statuses": [trace.status.value for trace in result.traces],
                    },
                )
            )
        self._maybe_fail_after_step("tool_stage")
        return RuntimeStepResult(next_step="finalize")

    async def _execute_finalize(self, context: _RunContext) -> RuntimeStepResult:
        if context.llm_response is None and isinstance(
            context.metadata.get("last_llm_response"), dict
        ):
            context.llm_response = LlmResponse.model_validate(
                context.metadata["last_llm_response"]
            )
        finish_reason = (
            context.llm_response.finish_reason.value
            if context.llm_response
            else "unknown"
        )
        self._emit(
            _EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_COMPLETED,
                payload={"finish_reason": finish_reason},
            )
        )
        output = self._build_output(
            context,
            _TerminalResult(
                status=RunStatus.COMPLETED,
                reason=TerminalReason.FINAL_ANSWER,
            ),
        )
        context.step_count += 1
        context.metadata.update(
            {
                "next_step": "done",
                "step_count": context.step_count,
                "tool_calls": context.tool_calls,
            }
        )
        output.checkpoint = self._save_checkpoint(
            context,
            latest_output=output,
            node_id="finalize",
        )
        self._maybe_fail_after_step("finalize")
        context.metadata["terminal_output"] = output.model_dump(mode="json")
        return RuntimeStepResult(next_step="done")

    async def _execute_step(self, context: _RunContext) -> RuntimeStepResult:
        if context.step_name == "run_started":
            return await self._execute_run_started(context)
        if context.step_name == "llm_call":
            return await self._execute_llm_call(context)
        if context.step_name == "tool_stage":
            return await self._execute_tool_stage(context)
        if context.step_name == "finalize":
            return await self._execute_finalize(context)
        raise RuntimeExecutionError(f"Unknown step '{context.step_name}'")

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
                    _EventSpec(
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
