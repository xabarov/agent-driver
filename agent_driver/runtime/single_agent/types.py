"""Internal types for SingleAgentRunner (step loop, deps, pending interrupt)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from time import monotonic
from typing import Any

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
from agent_driver.memory.provider import MemoryProvider
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.control.protocols import CommandQueueStore
from agent_driver.runtime.lifecycle_hooks import RunLifecycleHook
from agent_driver.runtime.metadata_state import (
    get_loop_control_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.lifecycle.config_sections import (
    CodeAgentSettings,
    CompactionSettings,
    PythonToolSettings,
    SubagentSettings,
    TrimmingSettings,
)
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.runtime.tools import ToolExecutor
from agent_driver.subagents.mailbox import SubagentMailboxStore
from agent_driver.subagents.store import SubagentStore
from agent_driver.tools.registry import ToolRegistry

_TRIMMING_FIELDS = {item.name for item in fields(TrimmingSettings)}
_COMPACTION_FIELDS = {item.name for item in fields(CompactionSettings)}
_SUBAGENT_FIELDS = {item.name for item in fields(SubagentSettings)}
_CODE_AGENT_FIELDS = {item.name for item in fields(CodeAgentSettings)}
_PYTHON_TOOL_FIELDS = {item.name for item in fields(PythonToolSettings)}


@dataclass(init=False)
class RunnerConfig:
    """Configuration for durable single-agent runtime runner.

    Intentionally **not** ``slots=True``: with a custom ``__init__`` the slotted
    variant required every field to be declared in two synchronized places
    (the slot annotation and the assignment), and a missed slot raised an
    ``AttributeError`` at construction. Without slots, adding a config field is
    a single assignment in ``__init__``. The annotations below remain as
    documentation and power dataclass ``repr``/``eq``.
    """

    graph_id: str
    cancellation_probe: Callable[[], bool] | None
    fail_after_step: str | None
    tool_executor: ToolExecutor | None
    session_store: SessionStore | None
    artifact_store: ArtifactStore | None
    context_store: ContextStore | None
    observation_max_chars: int
    include_planning_prompt: bool
    subagent_store: SubagentStore | None
    subagent_mailbox_store: SubagentMailboxStore | None
    code_executor: CodeActionExecutor | None
    tool_registry: ToolRegistry | None
    command_queue_store: CommandQueueStore | None
    memory_provider: MemoryProvider | None
    enable_prompt_cache: bool
    lifecycle_hooks: tuple[RunLifecycleHook, ...]
    trimming: TrimmingSettings
    compaction: CompactionSettings
    subagents: SubagentSettings
    code_agent: CodeAgentSettings
    python_tool: PythonToolSettings

    def __init__(self, **kwargs: Any) -> None:
        trimming = kwargs.pop("trimming", None) or TrimmingSettings(
            **{key: kwargs.pop(key) for key in list(kwargs) if key in _TRIMMING_FIELDS}
        )
        compaction = kwargs.pop("compaction", None) or CompactionSettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _COMPACTION_FIELDS
            }
        )
        subagents = kwargs.pop("subagents", None) or SubagentSettings(
            **{key: kwargs.pop(key) for key in list(kwargs) if key in _SUBAGENT_FIELDS}
        )
        code_agent = kwargs.pop("code_agent", None) or CodeAgentSettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _CODE_AGENT_FIELDS
            }
        )
        python_tool = kwargs.pop("python_tool", None) or PythonToolSettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _PYTHON_TOOL_FIELDS
            }
        )
        self.graph_id = kwargs.pop("graph_id", "single_agent_runtime")
        self.cancellation_probe = kwargs.pop("cancellation_probe", None)
        self.fail_after_step = kwargs.pop("fail_after_step", None)
        self.tool_executor = kwargs.pop("tool_executor", None)
        self.session_store = kwargs.pop("session_store", None)
        self.artifact_store = kwargs.pop("artifact_store", None)
        self.context_store = kwargs.pop("context_store", None)
        self.observation_max_chars = kwargs.pop("observation_max_chars", 400)
        self.include_planning_prompt = kwargs.pop("include_planning_prompt", False)
        self.subagent_store = kwargs.pop("subagent_store", None)
        self.subagent_mailbox_store = kwargs.pop("subagent_mailbox_store", None)
        self.code_executor = kwargs.pop("code_executor", None)
        self.tool_registry = kwargs.pop("tool_registry", None)
        self.command_queue_store = kwargs.pop("command_queue_store", None)
        self.memory_provider = kwargs.pop("memory_provider", None)
        self.enable_prompt_cache = bool(kwargs.pop("enable_prompt_cache", False))
        self.lifecycle_hooks = tuple(kwargs.pop("lifecycle_hooks", ()) or ())
        self.trimming = trimming
        self.compaction = compaction
        self.subagents = subagents
        self.code_agent = code_agent
        self.python_tool = python_tool
        if kwargs:
            raise TypeError(f"Unexpected RunnerConfig arguments: {sorted(kwargs)}")

    def with_overrides(self, **overrides: Any) -> "RunnerConfig":
        """Return a shallow copy with top-level attribute overrides applied.

        Shallow by design: callers only reassign top-level attributes
        (``tool_registry``, ``tool_executor``, ``memory_provider``,
        ``command_queue_store``); nested settings objects are shared but never
        mutated. This avoids ``deepcopy``, which cannot copy stateful deps such
        as a memory provider's DB connection or lock.
        """
        clone = copy.copy(self)
        for key, value in overrides.items():
            setattr(clone, key, value)
        return clone

    @property
    def trim_max_chars(self) -> int:
        return self.trimming.trim_max_chars

    @property
    def trim_max_messages(self) -> int | None:
        return self.trimming.trim_max_messages

    @property
    def trim_max_observations(self) -> int | None:
        return self.trimming.trim_max_observations

    @property
    def microcompact_preserve_recent(self) -> int:
        return self.trimming.microcompact_preserve_recent

    @property
    def microcompact_max_preview_chars(self) -> int:
        return self.trimming.microcompact_max_preview_chars

    @property
    def context_window_estimate(self) -> int:
        return self.trimming.context_window_estimate

    @property
    def token_warning_threshold(self) -> int:
        return self.trimming.token_warning_threshold

    @property
    def token_compact_threshold(self) -> int:
        return self.trimming.token_compact_threshold

    @property
    def token_blocking_threshold(self) -> int:
        return self.trimming.token_blocking_threshold

    @property
    def output_token_reserve(self) -> int:
        return self.trimming.output_token_reserve

    @property
    def enable_compaction(self) -> bool:
        return self.compaction.enable_compaction

    @property
    def enable_session_memory_compaction(self) -> bool:
        return self.compaction.enable_session_memory_compaction

    @property
    def enable_llm_compaction(self) -> bool:
        return self.compaction.enable_llm_compaction

    @property
    def enable_partial_compaction(self) -> bool:
        return self.compaction.enable_partial_compaction

    @property
    def enable_ptl_retry(self) -> bool:
        return self.compaction.enable_ptl_retry

    @property
    def compaction_failure_limit(self) -> int:
        return self.compaction.compaction_failure_limit

    @property
    def session_memory_stale_after_turns(self) -> int:
        return self.compaction.session_memory_stale_after_turns

    @property
    def compaction_model(self) -> str:
        return self.compaction.compaction_model

    @property
    def ptl_retry_max_chars(self) -> int:
        return self.compaction.ptl_retry_max_chars

    @property
    def post_compact_max_reinjected_artifact_refs(self) -> int:
        return self.compaction.post_compact_max_reinjected_artifact_refs

    @property
    def enable_subagents(self) -> bool:
        return self.subagents.enable_subagents

    @property
    def max_child_runs(self) -> int:
        return self.subagents.max_child_runs

    @property
    def default_child_deadline_seconds(self) -> float | None:
        return self.subagents.default_child_deadline_seconds

    @property
    def code_limits(self):
        return self.code_agent.code_limits

    @property
    def authorized_imports(self) -> tuple[str, ...]:
        return self.code_agent.authorized_imports


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
    # Optional caller-supplied abort signal. Polled at step boundaries
    # (see ``_terminal_from_limits``). Lives outside ``AgentRunInput``
    # because it holds a live threading lock + WeakSet that don't
    # belong in a JSON-serialisable transport contract.
    abort_handle: "RunAbortHandle | None" = None
    # Optional caller-supplied per-call gate (A0.2). Consulted in
    # ``GovernedToolExecutor._execute_one_call`` AFTER the static
    # ``ToolPolicyInput`` pass returns ALLOW; the gate can flip the
    # decision to DENY (blocked envelope) or INTERRUPT (operator
    # approval). Lives on RunContext for the same reason as
    # ``abort_handle`` — callables don't belong on a JSON-serialisable
    # transport contract.
    tool_gate: "ToolGate | None" = None

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
        return get_loop_control_state(self).next_step

    @step_name.setter
    def step_name(self, value: str) -> None:
        get_loop_control_state(self).next_step = value

    @property
    def step_count(self) -> int:
        """Executed transition count in current run."""
        return get_loop_control_state(self).step_count

    @step_count.setter
    def step_count(self, value: int) -> None:
        get_loop_control_state(self).step_count = value

    @property
    def tool_calls(self) -> int:
        """Accumulated tool-call count across tool stages."""
        return get_tool_loop_state(self).tool_calls

    @tool_calls.setter
    def tool_calls(self, value: int) -> None:
        get_tool_loop_state(self).tool_calls = value

    @property
    def llm_step_count(self) -> int:
        """Count of completed LLM-call iterations (used for max_steps budget)."""
        return get_loop_control_state(self).llm_step_count

    @llm_step_count.setter
    def llm_step_count(self, value: int) -> None:
        get_loop_control_state(self).llm_step_count = value


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
class RunnerDeps:
    """External dependencies for the runner loop."""

    provider: LlmProvider
    checkpoint_store: CheckpointStore
    event_log: RuntimeEventLog
    tool_executor: ToolExecutor
    session_store: SessionStore
    artifact_store: ArtifactStore
    context_store: ContextStore
    subagent_store: SubagentStore
    subagent_mailbox_store: SubagentMailboxStore | None
    code_executor: CodeActionExecutor
    tool_registry: ToolRegistry
    command_queue_store: CommandQueueStore | None = None
    python_backend: Any | None = None
    lifecycle_hooks: tuple[RunLifecycleHook, ...] = ()


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
