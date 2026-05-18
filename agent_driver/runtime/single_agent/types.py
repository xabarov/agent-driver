"""Internal types for SingleAgentRunner (step loop, deps, pending interrupt)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.code_agent.executor import CodeActionExecutor
from agent_driver.context.artifacts import ArtifactStore, ContextStore
from agent_driver.context.sessions import SessionStore
from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import RunStatus, RuntimeEventType, TerminalReason
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.providers import LlmProvider
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tools import ToolExecutor
from agent_driver.tools.registry import ToolRegistry


@dataclass(slots=True)
class RunnerConfig:  # pylint: disable=too-many-instance-attributes
    """Configuration for durable single-agent runtime runner."""

    graph_id: str = "single_agent_runtime"
    cancellation_probe: Callable[[], bool] | None = None
    fail_after_step: str | None = None
    tool_executor: ToolExecutor | None = None
    session_store: SessionStore | None = None
    artifact_store: ArtifactStore | None = None
    context_store: ContextStore | None = None
    observation_max_chars: int = 400
    trim_max_chars: int = 6000
    trim_max_messages: int | None = 24
    code_executor: CodeActionExecutor | None = None
    code_limits: CodeAgentLimits = field(default_factory=CodeAgentLimits)
    authorized_imports: tuple[str, ...] = ()
    tool_registry: ToolRegistry | None = None


@dataclass(slots=True)
class RuntimeStepResult:
    """Internal step transition result."""

    next_step: str


@dataclass(slots=True)
class RunContext:
    """Mutable execution context for one runner loop."""

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
class EventSpec:
    """Structured emit spec for runtime events."""

    run_id: str
    attempt_id: str
    event_type: RuntimeEventType
    payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Resolved terminal status for one outcome."""

    status: RunStatus
    reason: TerminalReason


@dataclass(frozen=True, slots=True)
class RunnerDeps:  # pylint: disable=too-many-instance-attributes
    """External dependencies for the runner loop."""

    provider: LlmProvider
    checkpoint_store: CheckpointStore
    event_log: RuntimeEventLog
    tool_executor: ToolExecutor
    session_store: SessionStore
    artifact_store: ArtifactStore
    context_store: ContextStore
    code_executor: CodeActionExecutor
    tool_registry: ToolRegistry


@dataclass(slots=True)
class PendingInterruptState:
    """Pending interrupt state kept in checkpoint metadata."""

    interrupt: InterruptRequest
    call: ToolCall
    envelope: ToolResultEnvelope


__all__ = [
    "EventSpec",
    "PendingInterruptState",
    "RunContext",
    "RunnerConfig",
    "RunnerDeps",
    "RuntimeStepResult",
    "TerminalResult",
]
