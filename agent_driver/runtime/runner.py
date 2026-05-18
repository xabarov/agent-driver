"""Fake single-step runner over LLM gateway and runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.events import (
    RuntimeEvent,
    RuntimeEventContext,
    new_runtime_event,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.state import RuntimeState


@dataclass(slots=True)
class RunnerConfig:
    """Configuration for deterministic single-step runtime runner."""

    graph_id: str = "single_step_fake"


class FakeSingleStepRunner:
    """Deterministic one-step runtime runner for checkpoint/event invariants."""

    def __init__(
        self,
        *,
        provider: LlmProvider,
        checkpoint_store: InMemoryCheckpointStore,
        event_log: InMemoryEventLog,
        config: RunnerConfig | None = None,
    ) -> None:
        cfg = config or RunnerConfig()
        self._provider = provider
        self._checkpoint_store = checkpoint_store
        self._event_log = event_log
        self._graph_id = cfg.graph_id

    @property
    def graph_id(self) -> str:
        """Expose configured graph id for diagnostics/tests."""
        return self._graph_id

    async def run(self, run_input: AgentRunInput) -> AgentRunOutput:
        """Execute one LLM completion step and persist checkpoint/events."""
        run_id = run_input.run_id or f"run_{uuid4().hex}"
        attempt_id = f"attempt_{uuid4().hex[:8]}"
        prompt = run_input.input or (
            run_input.messages[-1].content if run_input.messages else ""
        )
        llm_response = await self._provider.complete(
            LlmRequest(
                messages=[ChatMessage(role="user", content=prompt)],
                model_role=run_input.model_role,
                model=run_input.tool_policy.metadata.get("forced_model"),
                stream=False,
            )
        )
        events: list[RuntimeEvent] = [
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context=RuntimeEventContext(
                    run_id=run_id, attempt_id=attempt_id, seq=1
                ),
                options={"payload": {"agent_id": run_input.agent_id}},
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context=RuntimeEventContext(
                    run_id=run_id, attempt_id=attempt_id, seq=2
                ),
                options={
                    "payload": {"finish_reason": llm_response.finish_reason.value}
                },
            ),
        ]
        for event in events:
            self._event_log.append(event)
        output = AgentRunOutput(
            run_id=run_id,
            attempt_id=attempt_id,
            thread_id=run_input.thread_id,
            status=RunStatus.COMPLETED,
            answer=llm_response.message.content,
            messages=[llm_response.message],
            events=events,
            usage=llm_response.usage,
            terminal_reason=TerminalReason.FINAL_ANSWER,
            metadata={"graph_id": self._graph_id},
        )
        state = RuntimeState(
            run_input=run_input, latest_output=output, events=events, metadata={}
        )
        checkpoint = self._checkpoint_store.save(
            graph_id=self._graph_id, node_id="finalize", state=state
        )
        output.checkpoint = checkpoint
        return output
